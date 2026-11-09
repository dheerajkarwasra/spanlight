"""Remove departed members' API-key read access without stopping telemetry senders."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.scopes import READ_KEY_SCOPES
from app.db.models import ApiKey, AuditAction, Project
from app.services.audit import record_audit


@dataclass
class KeyStrip:
    revoked: int
    narrowed: int
    prefixes: list[str]


async def strip_read_keys(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    ip: str | None,
    reason: str,
) -> KeyStrip:
    """Caller holds a lock on the org row and commits; expired but unrevoked keys are included."""
    keys = await db.scalars(
        select(ApiKey)
        .where(
            ApiKey.created_by == user_id,
            ApiKey.revoked_at.is_(None),
            ApiKey.project_id.in_(select(Project.id).where(Project.org_id == org_id)),
        )
        .order_by(ApiKey.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    result = KeyStrip(revoked=0, narrowed=0, prefixes=[])
    now = datetime.now(UTC)
    for key in keys:
        remaining = [scope for scope in key.scopes if scope not in READ_KEY_SCOPES]
        if remaining == key.scopes:
            continue
        metadata: dict[str, Any] = {
            "name": key.name,
            "prefix": key.prefix,
            "project_id": str(key.project_id),
            "reason": reason,
            "member_user_id": str(user_id),
        }
        if remaining:
            metadata.update({"from": key.scopes, "to": remaining})
            key.scopes = remaining
            action = AuditAction.KEY_SCOPES_CHANGE
            result.narrowed += 1
        else:
            key.revoked_at = now
            action = AuditAction.KEY_REVOKE
            result.revoked += 1
        await record_audit(
            db,
            org_id=org_id,
            actor_user_id=actor_user_id,
            action=action,
            target_type="api_key",
            target_id=key.id,
            ip=ip,
            metadata=metadata,
        )
        result.prefixes.append(key.prefix)
    await db.flush()
    return result
