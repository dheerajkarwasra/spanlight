"""Prompt reads and the few direct writes. Every query runs in a transaction bound to the project,
so row-level security filters it; the `project_id` and `prompt_id` conditions are the second
line."""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Text, and_, cast, delete, exists, func, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.model_match import like_prefix
from app.db.models import (
    Evaluator,
    Experiment,
    Prompt,
    PromptKind,
    PromptLabel,
    PromptVersion,
)


@dataclass(frozen=True, slots=True)
class PromptRow:
    """A prompt with its latest version's number and kind."""

    prompt: Prompt
    latest_version: int
    kind: PromptKind


@dataclass(frozen=True, slots=True)
class LabelRow:
    prompt_id: uuid.UUID
    label: str
    version: int
    version_id: uuid.UUID


async def list_prompts(
    db: AsyncSession,
    project_id: uuid.UUID,
    *,
    prefix: str | None,
    after: tuple[datetime, uuid.UUID] | None,
    limit: int,
) -> list[PromptRow]:
    """Most recently changed first, up to `limit + 1` rows so the caller sees whether another
    page follows. `prefix` matches the start of the name."""
    latest = (
        select(PromptVersion.version, PromptVersion.kind)
        .where(PromptVersion.prompt_id == Prompt.id)
        .order_by(PromptVersion.version.desc())
        .limit(1)
        .lateral("latest")
    )
    query = (
        select(Prompt, latest.c.version, latest.c.kind)
        .join(latest, true())
        .where(Prompt.project_id == project_id)
        .order_by(Prompt.updated_at.desc(), Prompt.id.desc())
        .limit(limit + 1)
    )
    if prefix:
        query = query.where(Prompt.name.like(like_prefix(prefix), escape="\\"))
    if after is not None:
        moment, last_id = after
        query = query.where(
            or_(
                Prompt.updated_at < moment,
                and_(Prompt.updated_at == moment, Prompt.id < last_id),
            )
        )
    return [PromptRow(row[0], int(row[1]), row[2]) for row in await db.execute(query)]


async def get_prompt(
    db: AsyncSession, project_id: uuid.UUID, name: str, *, lock: bool = False
) -> Prompt | None:
    """The prompt of that exact name. `lock` takes its row lock, which serializes version
    numbering."""
    query = select(Prompt).where(Prompt.project_id == project_id, Prompt.name == name)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    return await db.scalar(query)


async def latest_version(db: AsyncSession, prompt_id: uuid.UUID) -> PromptVersion | None:
    return await db.scalar(
        select(PromptVersion)
        .where(PromptVersion.prompt_id == prompt_id)
        .order_by(PromptVersion.version.desc())
        .limit(1)
    )


async def get_version(db: AsyncSession, prompt_id: uuid.UUID, version: int) -> PromptVersion | None:
    return await db.scalar(
        select(PromptVersion).where(
            PromptVersion.prompt_id == prompt_id, PromptVersion.version == version
        )
    )


async def list_versions(
    db: AsyncSession, prompt_id: uuid.UUID, *, before: int | None, limit: int
) -> list[PromptVersion]:
    """Newest first, up to `limit + 1` rows; `before` is the last version of the previous page."""
    query = (
        select(PromptVersion)
        .where(PromptVersion.prompt_id == prompt_id)
        .order_by(PromptVersion.version.desc())
        .limit(limit + 1)
    )
    if before is not None:
        query = query.where(PromptVersion.version < before)
    return list(await db.scalars(query))


async def count_versions_since(db: AsyncSession, prompt_id: uuid.UUID, since: datetime) -> int:
    count = await db.scalar(
        select(func.count())
        .select_from(PromptVersion)
        .where(PromptVersion.prompt_id == prompt_id, PromptVersion.created_at >= since)
    )
    return int(count or 0)


async def labels_of(db: AsyncSession, prompt_ids: list[uuid.UUID]) -> list[LabelRow]:
    """Every label of these prompts, by label name, with the number of the version it names."""
    if not prompt_ids:
        return []
    rows = await db.execute(
        select(
            PromptLabel.prompt_id,
            PromptLabel.label,
            PromptVersion.version,
            PromptLabel.version_id,
        )
        .join(PromptVersion, PromptVersion.id == PromptLabel.version_id)
        .where(PromptLabel.prompt_id.in_(prompt_ids))
        .order_by(PromptLabel.label)
    )
    return [LabelRow(row[0], row[1], int(row[2]), row[3]) for row in rows]


async def get_label(db: AsyncSession, prompt_id: uuid.UUID, label: str) -> PromptLabel | None:
    return await db.get(PromptLabel, (prompt_id, label), with_for_update=True)


async def delete_label(db: AsyncSession, prompt_id: uuid.UUID, label: str) -> bool:
    """Remove the label; False when the prompt has none of that name."""
    result = await db.execute(
        delete(PromptLabel).where(PromptLabel.prompt_id == prompt_id, PromptLabel.label == label)
    )
    return bool(getattr(result, "rowcount", 0))


async def resolve(
    db: AsyncSession,
    project_id: uuid.UUID,
    name: str,
    *,
    label: str | None,
    version: int | None,
) -> PromptVersion | None:
    """The version a client reads: `version` when given, else the version `label` names
    (default `production`; `latest` is the highest version). None when any of them is unknown."""
    prompt = await get_prompt(db, project_id, name)
    if prompt is None:
        return None
    if version is not None:
        return await get_version(db, prompt.id, version)
    wanted = label or "production"
    if wanted == "latest":
        return await latest_version(db, prompt.id)
    return await db.scalar(
        select(PromptVersion)
        .join(PromptLabel, PromptLabel.version_id == PromptVersion.id)
        .where(PromptLabel.prompt_id == prompt.id, PromptLabel.label == wanted)
    )


async def version_in_project(
    db: AsyncSession, project_id: uuid.UUID, version_id: uuid.UUID, *, share_lock: bool = False
) -> PromptVersion | None:
    """The version, when it belongs to a prompt of the project. `share_lock` takes the prompt's
    row lock `FOR SHARE` until the transaction ends, so the prompt cannot be deleted (which
    locks it `FOR UPDATE`) while the caller stores a reference to the version."""
    query = (
        select(PromptVersion)
        .join(Prompt, Prompt.id == PromptVersion.prompt_id)
        .where(PromptVersion.id == version_id, Prompt.project_id == project_id)
    )
    if share_lock:
        query = query.with_for_update(read=True, of=Prompt)
    return await db.scalar(query)


async def version_with_prompt_name(
    db: AsyncSession, project_id: uuid.UUID, version_id: uuid.UUID
) -> tuple[str, PromptVersion] | None:
    """The version and its prompt's name, when it belongs to a prompt of the project."""
    row = (
        await db.execute(
            select(Prompt.name, PromptVersion)
            .join(Prompt, Prompt.id == PromptVersion.prompt_id)
            .where(PromptVersion.id == version_id, Prompt.project_id == project_id)
        )
    ).first()
    return None if row is None else (row[0], row[1])


async def prompt_referenced(db: AsyncSession, prompt_id: uuid.UUID) -> bool:
    """Whether anything that must stay reproducible uses one of the prompt's versions.

    The single place that knows the referrers, so deleting a prompt can refuse while one exists:
    a judge evaluator whose `config.prompt_version_id`, or an experiment whose
    `config.target.prompt_version_id`, names one of the versions.
    """
    version_ids = select(cast(PromptVersion.id, Text)).where(PromptVersion.prompt_id == prompt_id)
    used_by_evaluator = exists().where(
        Evaluator.config["prompt_version_id"].astext.in_(version_ids)
    )
    used_by_experiment = exists().where(
        Experiment.config["target"]["prompt_version_id"].astext.in_(version_ids)
    )
    return bool(await db.scalar(select(or_(used_by_evaluator, used_by_experiment))))
