"""Gateway keys: create, edit and revoke them. Callers commit.

A key's secret exists in clear only in `create_key`'s return value, which the router shows once.
It is never stored (only its SHA-256 is), logged or audited: the audit metadata names the key by
its prefix, which is safe to show.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import GATEWAY_KEY_PREFIX, generate_key
from app.db.models import AuditAction, GatewayKey
from app.gateway import key_queries
from app.gateway.key_errors import (
    FaultProfileOnProductionKeyError,
    KeyFaultProfileNotFoundError,
    KeyNotFoundError,
    KeyRouteNotFoundError,
    NoDefaultRouteError,
)
from app.gateway.key_schemas import GatewayKeyCreate, GatewayKeyUpdate, is_production_environment
from app.services.audit import record_audit


async def create_key(
    db: AsyncSession,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    actor_id: uuid.UUID,
    data: GatewayKeyCreate,
    *,
    now: datetime,
    ip: str | None = None,
) -> tuple[GatewayKey, str]:
    """Store a new key and return it with its full secret, which is never available again.

    Without `data.route_id` the key uses the project's default route. Raises
    `KeyRouteNotFoundError` for a route that is not the project's and `NoDefaultRouteError` when
    no route is named and the project has no default.
    """
    route_id = await _locked_route(db, project_id, data.route_id)
    generated = generate_key(GATEWAY_KEY_PREFIX)
    key = GatewayKey(
        project_id=project_id,
        route_id=route_id,
        name=data.name,
        prefix=generated.prefix,
        secret_hash=generated.secret_hash,
        environment=data.environment,
        rpm_limit=data.rpm_limit,
        tpm_limit=data.tpm_limit,
        allowed_models=list(data.allowed_models),
        default_tags=list(data.default_tags),
        cache_ttl_seconds=data.cache_ttl_seconds,
        created_by=actor_id,
        created_at=now,
    )
    db.add(key)
    await db.flush()
    await _audit(
        db,
        org_id,
        actor_id,
        AuditAction.GATEWAY_KEY_CREATE,
        key,
        ip,
        {
            "route_id": str(route_id),
            "environment": key.environment,
            "rpm_limit": key.rpm_limit,
            "tpm_limit": key.tpm_limit,
            "allowed_models": key.allowed_models,
            "default_tags": key.default_tags,
            "cache_ttl_seconds": key.cache_ttl_seconds,
        },
    )
    return key, generated.plaintext


async def update_key(
    db: AsyncSession,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    key_id: uuid.UUID,
    actor_id: uuid.UUID,
    data: GatewayKeyUpdate,
    *,
    ip: str | None = None,
) -> GatewayKey:
    """Apply the fields `data` carries. An edit that changes nothing audits nothing.

    A revoked key cannot be edited. A fault profile never runs on a `production` key, whether the
    profile is attached to one or the key is moved there while it has one; the key row is locked
    for the check, so two concurrent edits cannot each pass it. Raises `KeyNotFoundError`,
    `KeyRouteNotFoundError`, `KeyFaultProfileNotFoundError` and
    `FaultProfileOnProductionKeyError`.
    """
    key = await key_queries.lock_key(db, project_id, key_id)
    if key is None or key.revoked_at is not None:
        raise KeyNotFoundError
    changed = {
        field: value for field, value in data.changes().items() if getattr(key, field) != value
    }
    if not changed:
        return key
    route_id = changed.get("route_id")
    if route_id is not None and not await key_queries.share_lock_route(db, project_id, route_id):
        raise KeyRouteNotFoundError
    profile_id = changed.get("fault_profile_id")
    if profile_id is not None and not await key_queries.share_lock_fault_profile(
        db, project_id, profile_id
    ):
        raise KeyFaultProfileNotFoundError
    _check_fault_profile_allowed(key, changed)
    for field, value in changed.items():
        setattr(key, field, list(value) if isinstance(value, list) else value)
    await db.flush()
    await _audit(
        db,
        org_id,
        actor_id,
        AuditAction.GATEWAY_KEY_UPDATE,
        key,
        ip,
        {"changes": {field: _jsonable(value) for field, value in changed.items()}},
    )
    return key


async def revoke_key(
    db: AsyncSession,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    key_id: uuid.UUID,
    actor_id: uuid.UUID,
    *,
    now: datetime,
    ip: str | None = None,
) -> None:
    """Revoke the key. Raises `KeyNotFoundError`.

    The handler clears this process's key cache, so its gateway refuses the key from the next
    call; another api or gateway process refuses it within `api_key_cache_ttl_seconds`
    (`app.gateway.key_context`).

    Revoking a revoked key changes nothing and audits nothing. The row stays, so the spans sent
    with the key keep their attribution.
    """
    key = await key_queries.lock_key(db, project_id, key_id)
    if key is None:
        raise KeyNotFoundError
    if key.revoked_at is not None:
        return
    key.revoked_at = now
    await db.flush()
    await _audit(db, org_id, actor_id, AuditAction.GATEWAY_KEY_REVOKE, key, ip, {})


async def release_route(db: AsyncSession, project_id: uuid.UUID, route_id: uuid.UUID) -> None:
    """Clear the route from the project's revoked keys, so the route can be deleted.

    For `route_service.delete_route`, after it has checked that no active key uses the route.
    """
    await db.execute(
        update(GatewayKey)
        .where(
            GatewayKey.project_id == project_id,
            GatewayKey.route_id == route_id,
            GatewayKey.revoked_at.is_not(None),
        )
        .values(route_id=None)
        .execution_options(synchronize_session=False)
    )


def _check_fault_profile_allowed(key: GatewayKey, changed: dict[str, Any]) -> None:
    """Refuse an edit that leaves a fault profile on a `production` key."""
    if "environment" not in changed and "fault_profile_id" not in changed:
        return
    environment = changed.get("environment", key.environment)
    profile_id = changed.get("fault_profile_id", key.fault_profile_id)
    if is_production_environment(environment) and profile_id is not None:
        raise FaultProfileOnProductionKeyError


async def _locked_route(
    db: AsyncSession, project_id: uuid.UUID, route_id: uuid.UUID | None
) -> uuid.UUID:
    """The route a new key uses, key-share locked until commit (see `share_lock_route`)."""
    if route_id is None:
        default = await key_queries.share_lock_default_route(db, project_id)
        if default is None:
            raise NoDefaultRouteError
        return default
    if not await key_queries.share_lock_route(db, project_id, route_id):
        raise KeyRouteNotFoundError
    return route_id


async def _audit(
    db: AsyncSession,
    org_id: uuid.UUID,
    actor_id: uuid.UUID,
    action: AuditAction,
    key: GatewayKey,
    ip: str | None,
    metadata: dict[str, Any],
) -> None:
    await record_audit(
        db,
        org_id=org_id,
        actor_user_id=actor_id,
        action=action,
        target_type="gateway_key",
        target_id=key.id,
        ip=ip,
        metadata={
            "name": key.name,
            "prefix": key.prefix,
            "project_id": str(key.project_id),
            **metadata,
        },
    )


def _jsonable(value: object) -> object:
    return str(value) if isinstance(value, uuid.UUID) else value
