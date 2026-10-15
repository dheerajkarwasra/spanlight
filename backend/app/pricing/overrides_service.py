"""Price overrides: add and delete an organization's own rates. Callers commit.

`price_overrides` has no row-level security, so the `org_id` condition in each statement is the
tenancy boundary. Each write takes the organization's key-share lock first, then writes the row
and its audit event: the lock order of `app.services.deletion`, so a concurrent organization
deletion waits instead of deadlocking.
"""

import uuid
from collections.abc import Mapping
from typing import Any

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import new_id
from app.db.models import AuditAction, PriceOverride
from app.services.audit import record_audit
from app.services.deletion import lock_org_for_write

AUDIT_TARGET = "price_override"
UNIQUE_CONSTRAINT = "price_overrides_org_model_key"


class PriceOverrideExistsError(Exception):
    """The organization already has an override for this provider, pattern, start time and tier."""


class PriceOverrideNotFoundError(Exception):
    """No override with this id in the organization (or the organization is gone)."""


async def create_override(
    db: AsyncSession,
    org_id: uuid.UUID,
    actor_id: uuid.UUID,
    values: Mapping[str, Any],
    *,
    ip: str | None = None,
) -> PriceOverride:
    """Add the override and audit it.

    Raises `PriceOverrideExistsError` and `PriceOverrideNotFoundError` (the organization is gone).
    """
    if not await lock_org_for_write(db, org_id):
        raise PriceOverrideNotFoundError
    result = await db.execute(
        insert(PriceOverride)
        .values(id=new_id(), org_id=org_id, created_by=actor_id, **values)
        .on_conflict_do_nothing(constraint=UNIQUE_CONSTRAINT)
        .returning(PriceOverride)
    )
    override = result.scalar_one_or_none()
    if override is None:
        raise PriceOverrideExistsError
    await _audit(db, org_id, actor_id, AuditAction.PRICE_OVERRIDE_CREATE, override, ip)
    return override


async def delete_override(
    db: AsyncSession,
    org_id: uuid.UUID,
    override_id: uuid.UUID,
    actor_id: uuid.UUID,
    *,
    ip: str | None = None,
) -> None:
    """Delete one of the organization's overrides and audit it.

    Raises `PriceOverrideNotFoundError`.
    """
    if not await lock_org_for_write(db, org_id):
        raise PriceOverrideNotFoundError
    result = await db.execute(
        delete(PriceOverride)
        .where(PriceOverride.org_id == org_id, PriceOverride.id == override_id)
        .returning(PriceOverride)
    )
    override = result.scalar_one_or_none()
    if override is None:
        raise PriceOverrideNotFoundError
    await _audit(db, org_id, actor_id, AuditAction.PRICE_OVERRIDE_DELETE, override, ip)


async def _audit(
    db: AsyncSession,
    org_id: uuid.UUID,
    actor_id: uuid.UUID,
    action: AuditAction,
    override: PriceOverride,
    ip: str | None,
) -> None:
    await record_audit(
        db,
        org_id=org_id,
        actor_user_id=actor_id,
        action=action,
        target_type=AUDIT_TARGET,
        target_id=override.id,
        ip=ip,
        metadata={
            "provider": override.provider,
            "model_pattern": override.model_pattern,
            "min_prompt_tokens": override.min_prompt_tokens,
        },
    )
