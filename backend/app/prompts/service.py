"""Prompts: create a prompt, add versions, move labels, delete. Callers commit.

Every write runs in a transaction bound to the project (row-level security). A version number is
assigned under the prompt's row lock, so concurrent saves number 2 and 3 without a gap. Versions
are never edited (ADR 0014). Audit metadata holds names and numbers, never template text.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.errors import violated_constraint
from app.db.models import AuditAction, Prompt, PromptKind, PromptLabel, PromptVersion
from app.prompts import queries
from app.prompts.errors import (
    KindMismatchError,
    LabelReservedError,
    NoChangesError,
    PromptInUseError,
    PromptNameTakenError,
    PromptNotFoundError,
    VersionRateLimitedError,
)
from app.prompts.queries import LabelRow
from app.prompts.schemas import PromptCreate, VersionBody, VersionCreate, to_template
from app.prompts.templates import extract_variables
from app.services.audit import record_audit

NAME_CONSTRAINT = "prompts_project_name_key"
MAX_VERSIONS_PER_DAY = 100
RESERVED_LABEL = "latest"


async def create_prompt(
    db: AsyncSession,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    actor_id: uuid.UUID,
    spec: PromptCreate,
    *,
    now: datetime,
    ip: str | None = None,
) -> tuple[Prompt, PromptVersion]:
    """Store the prompt and its version 1 together. Raises `InvalidTemplateError` and
    `PromptNameTakenError`."""
    variables = extract_variables(to_template(spec.template))
    prompt = Prompt(
        project_id=project_id,
        name=spec.name,
        description=spec.description,
        created_by=actor_id,
        created_at=now,
        updated_at=now,
    )
    db.add(prompt)
    try:
        async with db.begin_nested():
            await db.flush()
    except IntegrityError as error:
        if violated_constraint(error) == NAME_CONSTRAINT:
            raise PromptNameTakenError from None
        raise
    version = _new_version(prompt, 1, spec, variables, actor_id, now)
    db.add(version)
    await db.flush()
    await _audit(
        db,
        org_id,
        actor_id,
        AuditAction.PROMPT_CREATE,
        prompt,
        ip,
        {"name": prompt.name, "kind": version.kind.value},
    )
    return prompt, version


async def create_version(
    db: AsyncSession,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    actor_id: uuid.UUID,
    name: str,
    spec: VersionCreate,
    *,
    now: datetime,
    ip: str | None = None,
) -> PromptVersion:
    """Add the next version. Raises `InvalidTemplateError`, `PromptNotFoundError`,
    `KindMismatchError`, `NoChangesError` and `VersionRateLimitedError`."""
    variables = extract_variables(to_template(spec.template))
    prompt = await _locked(db, project_id, name)
    latest = await queries.latest_version(db, prompt.id)
    if latest is None:  # a prompt is created with its version 1; only a damaged row lacks one
        raise PromptNotFoundError
    if spec.template.kind != latest.kind.value:
        raise KindMismatchError
    await _refuse_unwanted(db, prompt, latest, spec, now)
    version = _new_version(prompt, latest.version + 1, spec, variables, actor_id, now)
    db.add(version)
    prompt.updated_at = now
    await db.flush()
    await _audit(
        db,
        org_id,
        actor_id,
        AuditAction.PROMPT_VERSION_CREATE,
        prompt,
        ip,
        {"name": prompt.name, "version": version.version},
    )
    return version


async def set_label(
    db: AsyncSession,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    actor_id: uuid.UUID,
    name: str,
    label: str,
    version: int,
    *,
    now: datetime,
    ip: str | None = None,
) -> LabelRow:
    """Point `label` at `version`, moving it if it exists. Raises `LabelReservedError` and
    `PromptNotFoundError` (also for a version the prompt does not have)."""
    if label == RESERVED_LABEL:
        raise LabelReservedError
    prompt = await _locked(db, project_id, name)
    target = await queries.get_version(db, prompt.id, version)
    if target is None:
        raise PromptNotFoundError
    await _point_label(db, prompt, label, target, actor_id, now)
    await _audit(
        db,
        org_id,
        actor_id,
        AuditAction.PROMPT_LABEL_SET,
        prompt,
        ip,
        {"name": prompt.name, "label": label, "version": version},
    )
    return LabelRow(prompt.id, label, version, target.id)


async def delete_label(
    db: AsyncSession,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    actor_id: uuid.UUID,
    name: str,
    label: str,
    *,
    now: datetime,
    ip: str | None = None,
) -> None:
    """Remove the label. Raises `PromptNotFoundError` for an unknown prompt or label."""
    prompt = await _locked(db, project_id, name)
    if not await queries.delete_label(db, prompt.id, label):
        raise PromptNotFoundError
    prompt.updated_at = now
    await db.flush()
    await _audit(
        db,
        org_id,
        actor_id,
        AuditAction.PROMPT_LABEL_DELETE,
        prompt,
        ip,
        {"name": prompt.name, "label": label},
    )


async def delete_prompt(
    db: AsyncSession,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    actor_id: uuid.UUID,
    name: str,
    *,
    ip: str | None = None,
) -> None:
    """Delete the prompt with its versions and labels. Raises `PromptNotFoundError`, and
    `PromptInUseError` while an evaluator (or an experiment) uses one of its versions.
    """
    prompt = await _locked(db, project_id, name)
    if await queries.prompt_referenced(db, prompt.id):
        raise PromptInUseError
    await db.delete(prompt)
    await db.flush()
    await _audit(db, org_id, actor_id, AuditAction.PROMPT_DELETE, prompt, ip, {"name": prompt.name})


async def project_version(
    db: AsyncSession, project_id: uuid.UUID, version_id: uuid.UUID, *, hold: bool = False
) -> PromptVersion | None:
    """The prompt version, when it belongs to a prompt of the project; None otherwise. `hold`
    keeps its prompt from being deleted until the transaction ends: for a caller that is about
    to store a reference to the version (an evaluator, an experiment)."""
    return await queries.version_in_project(db, project_id, version_id, share_lock=hold)


async def _refuse_unwanted(
    db: AsyncSession, prompt: Prompt, latest: PromptVersion, spec: VersionCreate, now: datetime
) -> None:
    """Raise `NoChangesError` for a version equal to the latest and `VersionRateLimitedError`
    past the daily limit. The caller holds the prompt's row lock, so the count is race-free."""
    if (
        latest.template == spec.template.model_dump()
        and latest.model_config == spec.advice.stored()
    ):
        raise NoChangesError
    day_start = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    if await queries.count_versions_since(db, prompt.id, day_start) >= MAX_VERSIONS_PER_DAY:
        retry_after = int((day_start + timedelta(days=1) - now).total_seconds())
        raise VersionRateLimitedError(retry_after)


async def _point_label(
    db: AsyncSession,
    prompt: Prompt,
    label: str,
    target: PromptVersion,
    actor_id: uuid.UUID,
    now: datetime,
) -> None:
    """Create `label` on `target` or move it there, and mark the prompt as changed."""
    existing = await queries.get_label(db, prompt.id, label)
    if existing is None:
        db.add(
            PromptLabel(
                prompt_id=prompt.id,
                label=label,
                version_id=target.id,
                updated_by=actor_id,
                updated_at=now,
            )
        )
    else:
        existing.version_id = target.id
        existing.updated_by = actor_id
        existing.updated_at = now
    prompt.updated_at = now
    await db.flush()


async def _audit(
    db: AsyncSession,
    org_id: uuid.UUID,
    actor_id: uuid.UUID,
    action: AuditAction,
    prompt: Prompt,
    ip: str | None,
    metadata: dict[str, Any],
) -> None:
    await record_audit(
        db,
        org_id=org_id,
        actor_user_id=actor_id,
        action=action,
        target_type="prompt",
        target_id=prompt.id,
        ip=ip,
        metadata=metadata,
    )


async def _locked(db: AsyncSession, project_id: uuid.UUID, name: str) -> Prompt:
    prompt = await queries.get_prompt(db, project_id, name, lock=True)
    if prompt is None:
        raise PromptNotFoundError
    return prompt


def _new_version(
    prompt: Prompt,
    number: int,
    spec: VersionBody,
    variables: list[str],
    actor_id: uuid.UUID,
    now: datetime,
) -> PromptVersion:
    template: dict[str, Any] = spec.template.model_dump()
    return PromptVersion(
        prompt_id=prompt.id,
        version=number,
        kind=PromptKind(spec.template.kind),
        template=template,
        variables=variables,
        model_config=spec.advice.stored(),
        commit_message=spec.commit_message,
        created_by=actor_id,
        created_at=now,
    )
