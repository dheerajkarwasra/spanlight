"""Billing sources: add, edit, rotate and delete them (spec §8.2). Callers commit.

Every write takes the organization's write lock first (`lock_org_for_write`), then the source
row, then writes the audit event: the lock order of `app.services.deletion`, so a concurrent
organization deletion waits instead of deadlocking.

Adding or rotating an API source runs one check of the key (`source_keys.check_key`, at most
10 s) before any lock is taken, so a slow provider never holds the organization's writes. The
transaction of the reads before it is committed first, so no pooled connection waits on the
provider idle in a transaction; what those reads decided is checked again under the locks (a
project taken meanwhile fails the mapping's unique constraint, a source deleted meanwhile is not
found). The source is stored whatever the check says; its outcome is the source's `status` and
`last_error`. The router throttles the checks per organization.

The clear admin key lives only in the request body and in the local variables of these
functions: it is sealed before it is stored, and never logged, audited or put in an exception.
"""

import uuid
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime
from typing import Any

import httpx
from pydantic import SecretStr
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.billing_sources import BillingSourceCreate, BillingSourceUpdate
from app.config import Settings
from app.core.crypto import encrypt
from app.core.ids import new_id
from app.db.models import AuditAction
from app.db.models.billing import BillingSource
from app.reconciliation import queries
from app.reconciliation.errors import BillingSourceNotFound, NotApiKind
from app.reconciliation.source_keys import check_key, record_check, require_crypto
from app.reconciliation.source_mapping import conflict_of, free_projects, map_projects
from app.reconciliation.source_rules import (
    admin_key_for,
    clean_admin_key,
    is_api_kind,
    provider_for,
)
from app.services.audit import record_audit
from app.services.deletion import lock_org_for_write


async def audit_source(
    db: AsyncSession,
    source: BillingSource,
    action: AuditAction,
    actor_id: uuid.UUID,
    ip: str | None,
    *,
    extra: Mapping[str, Any] | None = None,
) -> None:
    """Record `action` on the source: its kind, provider and projects, plus `extra` (ids and
    counts only, never a key or a file's content)."""
    project_ids = await queries.projects_of_source(db, source.org_id, source.id)
    await record_audit(
        db,
        org_id=source.org_id,
        actor_user_id=actor_id,
        action=action,
        target_type="billing_source",
        target_id=source.id,
        ip=ip,
        metadata={
            "kind": source.kind,
            "provider": source.provider,
            "project_ids": [str(project_id) for project_id in project_ids],
            **(extra or {}),
        },
    )


async def create_source(
    db: AsyncSession,
    org_id: uuid.UUID,
    actor_id: uuid.UUID,
    data: BillingSourceCreate,
    *,
    settings: Settings,
    http: httpx.AsyncClient,
    now: datetime,
    ip: str | None = None,
    before_check: Callable[[], Awaitable[None]] | None = None,
) -> BillingSource:
    """Store the source, its key sealed, mapped to `data.project_ids`, with the check's outcome.

    `before_check` runs once the request is known to be checkable (an API kind, crypto on, a
    well-formed key) and before the provider is asked: the route's throttle, so a refused
    request spends no token.

    Raises `InvalidSourceField`, `CryptoNotConfigured` (an API kind without CREDENTIALS_KEYS),
    `ProjectNotInOrg`, `ProjectTaken`, `NameTaken` and `BillingSourceNotFound` (the
    organization is gone).
    """
    provider = provider_for(data.kind, data.provider)
    if is_api_kind(data.kind):
        require_crypto(settings)
    key = clean_admin_key(data.kind, data.admin_key)
    if key is not None and before_check is not None:
        await before_check()
    project_ids = await free_projects(db, org_id, provider, data.project_ids)
    source_id = new_id()
    source = BillingSource(
        id=source_id,
        org_id=org_id,
        kind=data.kind,
        provider=provider,
        name=data.name,
        provider_scope=data.provider_scope,
        created_by=actor_id,
    )
    if key is not None:
        sealed = encrypt(key.encode(), settings=settings)
        source.ciphertext, source.key_id = sealed.ciphertext, sealed.key_id
        await db.commit()  # nothing written yet: only ends the reads' transaction
        record_check(source, await check_key(http, data.kind, key, settings=settings, now=now))

    if not await lock_org_for_write(db, org_id):
        raise BillingSourceNotFound
    try:
        async with db.begin_nested():
            db.add(source)
            await db.flush()
            await map_projects(db, source, project_ids)
    except IntegrityError as error:
        conflict = await conflict_of(
            db,
            error,
            org_id=org_id,
            provider=provider,
            source_id=source_id,
            project_ids=project_ids,
        )
        if conflict:
            raise conflict from None
        raise
    await audit_source(db, source, AuditAction.BILLING_SOURCE_CREATE, actor_id, ip)
    return source


async def update_source(
    db: AsyncSession,
    org_id: uuid.UUID,
    source_id: uuid.UUID,
    actor_id: uuid.UUID,
    data: BillingSourceUpdate,
    *,
    now: datetime,
    ip: str | None = None,
) -> BillingSource:
    """Change the fields sent; `project_ids` replaces the mapping. Raises
    `BillingSourceNotFound`, `ProjectNotInOrg`, `ProjectTaken` and `NameTaken`. An empty edit
    writes nothing, not even an audit event."""
    sent = data.model_fields_set
    if not sent:
        found = await queries.source_for_org(db, org_id, source_id)
        if found is None:
            raise BillingSourceNotFound
        return found
    if not await lock_org_for_write(db, org_id):
        raise BillingSourceNotFound
    source = await queries.source_for_org(db, org_id, source_id, lock=True)
    if source is None:
        raise BillingSourceNotFound
    provider = source.provider
    project_ids: list[uuid.UUID] | None = None
    if data.project_ids is not None:
        project_ids = await free_projects(
            db, org_id, provider, data.project_ids, except_source=source_id
        )
    try:
        async with db.begin_nested():
            if data.name is not None:
                source.name = data.name
            if "provider_scope" in sent:
                source.provider_scope = data.provider_scope
            if data.enabled is not None:
                source.enabled = data.enabled
            source.updated_at = now
            await db.flush()
            if project_ids is not None:
                await map_projects(db, source, project_ids)
    except IntegrityError as error:
        conflict = await conflict_of(
            db,
            error,
            org_id=org_id,
            provider=provider,
            source_id=source_id,
            project_ids=project_ids or [],
        )
        if conflict:
            raise conflict from None
        raise
    await audit_source(db, source, AuditAction.BILLING_SOURCE_UPDATE, actor_id, ip)
    return source


async def rotate_source(
    db: AsyncSession,
    org_id: uuid.UUID,
    source_id: uuid.UUID,
    actor_id: uuid.UUID,
    admin_key: SecretStr,
    *,
    settings: Settings,
    http: httpx.AsyncClient,
    now: datetime,
    ip: str | None = None,
    before_check: Callable[[], Awaitable[None]] | None = None,
) -> BillingSource:
    """Seal a new admin key in place of the old one and check it. `before_check` runs as on
    `create_source`. Raises `BillingSourceNotFound`, `NotApiKind`, `CryptoNotConfigured` and
    `InvalidSourceField`."""
    found = await queries.source_for_org(db, org_id, source_id)
    if found is None:
        raise BillingSourceNotFound
    kind = found.kind
    if not is_api_kind(kind):
        raise NotApiKind
    require_crypto(settings)
    key = admin_key_for(kind, admin_key)
    if before_check is not None:
        await before_check()
    sealed = encrypt(key.encode(), settings=settings)
    await db.commit()  # nothing written yet: only ends the reads' transaction
    checked = await check_key(http, kind, key, settings=settings, now=now)

    if not await lock_org_for_write(db, org_id):
        raise BillingSourceNotFound
    source = await queries.source_for_org(db, org_id, source_id, lock=True)
    if source is None:
        raise BillingSourceNotFound
    source.ciphertext, source.key_id = sealed.ciphertext, sealed.key_id
    record_check(source, checked)
    source.updated_at = now
    await db.flush()
    await audit_source(db, source, AuditAction.BILLING_SOURCE_ROTATE, actor_id, ip)
    return source


async def delete_source(
    db: AsyncSession,
    org_id: uuid.UUID,
    source_id: uuid.UUID,
    actor_id: uuid.UUID,
    *,
    ip: str | None = None,
) -> None:
    """Delete the source; its mappings, runs and lines go with it (foreign key cascades).
    Raises `BillingSourceNotFound`."""
    if not await lock_org_for_write(db, org_id):
        raise BillingSourceNotFound
    source = await queries.source_for_org(db, org_id, source_id, lock=True)
    if source is None:
        raise BillingSourceNotFound
    # Audited first: the event names the projects the source covered.
    await audit_source(db, source, AuditAction.BILLING_SOURCE_DELETE, actor_id, ip)
    await db.delete(source)
    await db.flush()
