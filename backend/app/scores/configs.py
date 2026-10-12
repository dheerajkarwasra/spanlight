"""Score configs: create, edit and delete what a score name means. Callers commit.

Every write runs in a transaction bound to the project (row-level security) and takes the
project's write lock first (through the router), then the config row. A config made or edited
here is explicit (`implicit = false`). Changing a config's type, or deleting it, is refused once
scores carry its name: their values were checked against the old meaning. Tightening a range or
a category list is allowed and applies to new scores only.

Audit metadata holds the config's name and type and the names of the fields that changed.
"""

import uuid
from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.errors import violated_constraint
from app.db.models import AuditAction, ScoreConfig
from app.scores import queries
from app.scores.errors import (
    ScoreConfigInUseError,
    ScoreConfigLimitError,
    ScoreConfigNameTakenError,
    ScoreConfigNotFoundError,
)
from app.scores.schemas import ScoreConfigPatch, ScoreConfigSpec
from app.services.audit import record_audit

NAME_CONSTRAINT = "score_configs_project_name_key"


async def create_config(
    db: AsyncSession,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    actor_id: uuid.UUID,
    spec: ScoreConfigSpec,
    *,
    now: datetime,
    ip: str | None = None,
) -> ScoreConfig:
    """Store a new explicit config. Raises `ScoreConfigNameTakenError`, and
    `ScoreConfigLimitError` when the project already has `MAX_CONFIGS` configs."""
    if await queries.count_configs(db, project_id) >= queries.MAX_CONFIGS:
        raise ScoreConfigLimitError
    config = ScoreConfig(
        project_id=project_id,
        implicit=False,
        created_at=now,
        updated_at=now,
        **spec.column_values(),
    )
    db.add(config)
    await _flush_named(db)
    await _audit(db, org_id, config, AuditAction.SCORE_CONFIG_CREATE, actor_id, ip=ip)
    return config


async def update_config(
    db: AsyncSession,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    config_id: uuid.UUID,
    actor_id: uuid.UUID,
    patch: ScoreConfigPatch,
    *,
    now: datetime,
    ip: str | None = None,
) -> ScoreConfig:
    """Apply a partial edit; the config becomes explicit. An edit that changes nothing writes no
    audit event. Raises `ScoreConfigNotFoundError` and `ScoreConfigInUseError` (a new type while
    scores use the name). A body that does not fit the type raises pydantic's `ValidationError`.
    """
    config = await queries.lock_config(db, project_id, config_id)
    if config is None:
        raise ScoreConfigNotFoundError
    values = patch.merged_with(spec_of(config)).column_values()
    changed = [column for column, value in values.items() if getattr(config, column) != value]
    if not changed:
        return config
    if "data_type" in changed and await queries.name_in_use(db, project_id, config.name):
        raise ScoreConfigInUseError
    for column in changed:
        setattr(config, column, values[column])
    config.implicit = False
    config.updated_at = now
    await db.flush()
    await _audit(
        db,
        org_id,
        config,
        AuditAction.SCORE_CONFIG_UPDATE,
        actor_id,
        ip=ip,
        extra={"changed": changed},
    )
    return config


async def delete_config(
    db: AsyncSession,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    config_id: uuid.UUID,
    actor_id: uuid.UUID,
    *,
    ip: str | None = None,
) -> None:
    """Delete a config no score uses. Raises `ScoreConfigNotFoundError` and
    `ScoreConfigInUseError`."""
    config = await queries.lock_config(db, project_id, config_id)
    if config is None:
        raise ScoreConfigNotFoundError
    if await queries.name_in_use(db, project_id, config.name):
        raise ScoreConfigInUseError
    await _audit(db, org_id, config, AuditAction.SCORE_CONFIG_DELETE, actor_id, ip=ip)
    await db.delete(config)
    await db.flush()


def spec_of(config: ScoreConfig) -> ScoreConfigSpec:
    """The config as stored, as a whole spec."""
    return ScoreConfigSpec.model_validate(
        {
            "name": config.name,
            "data_type": config.data_type,
            "min_value": config.min_value,
            "max_value": config.max_value,
            "categories": list(config.categories),
            "description": config.description,
        }
    )


async def _flush_named(db: AsyncSession) -> None:
    """Flush pending config changes; a name the project already has is
    `ScoreConfigNameTakenError`."""
    try:
        async with db.begin_nested():
            await db.flush()
    except IntegrityError as error:
        if violated_constraint(error) == NAME_CONSTRAINT:
            raise ScoreConfigNameTakenError from None
        raise


async def _audit(
    db: AsyncSession,
    org_id: uuid.UUID,
    config: ScoreConfig,
    action: AuditAction,
    actor_id: uuid.UUID,
    *,
    ip: str | None,
    extra: dict[str, object] | None = None,
) -> None:
    await record_audit(
        db,
        org_id=org_id,
        actor_user_id=actor_id,
        action=action,
        target_type="score_config",
        target_id=config.id,
        ip=ip,
        metadata={"name": config.name, "data_type": config.data_type.value, **(extra or {})},
    )
