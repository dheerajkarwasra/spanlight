"""Reads for the gateway domain. Provider credentials are org-scoped: every query filters by org.

`provider_credentials` has no row-level security, so the `org_id` condition in each query here
is the tenancy boundary. Gateway routes have their own module, `route_queries`. Nothing in this
module writes.
"""

import uuid
from collections.abc import Sequence

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import GatewayRoute, Project, ProviderCredential, User
from app.db.rls import bind_project


async def list_credentials(
    db: AsyncSession, org_id: uuid.UUID
) -> Sequence[tuple[ProviderCredential, User | None]]:
    """The organization's credentials with the user who added each, newest first.

    Unpaginated: an organization keeps a handful of provider keys, not thousands.
    """
    rows = await db.execute(
        select(ProviderCredential, User)
        .outerjoin(User, User.id == ProviderCredential.created_by)
        .where(ProviderCredential.org_id == org_id)
        .order_by(ProviderCredential.created_at.desc(), ProviderCredential.id.desc())
    )
    return [(credential, creator) for credential, creator in rows]


async def get_credential(
    db: AsyncSession, org_id: uuid.UUID, credential_id: uuid.UUID
) -> ProviderCredential | None:
    return await db.scalar(
        select(ProviderCredential).where(
            ProviderCredential.org_id == org_id, ProviderCredential.id == credential_id
        )
    )


async def get_credential_with_creator(
    db: AsyncSession, org_id: uuid.UUID, credential_id: uuid.UUID
) -> tuple[ProviderCredential, User | None] | None:
    row = (
        await db.execute(
            select(ProviderCredential, User)
            .outerjoin(User, User.id == ProviderCredential.created_by)
            .where(ProviderCredential.org_id == org_id, ProviderCredential.id == credential_id)
        )
    ).one_or_none()
    if row is None:
        return None
    credential, creator = row.tuple()
    return credential, creator


async def lock_credential(
    db: AsyncSession, org_id: uuid.UUID, credential_id: uuid.UUID
) -> ProviderCredential | None:
    """The credential, locked until the transaction ends, so two rotations do not interleave."""
    return await db.scalar(
        select(ProviderCredential)
        .where(ProviderCredential.org_id == org_id, ProviderCredential.id == credential_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


async def credential_in_use(db: AsyncSession, credential_id: uuid.UUID) -> str | None:
    """The name of a gateway route whose current config targets the credential, or None.

    Only a route's current config counts: an older version that names the credential cannot be
    reverted to once the credential is gone (the revert is refused with a `422`).

    Credentials belong to an organization, routes to its projects, and `gateway_routes` is under
    row-level security, so the routes of each of the organization's projects are read with that
    project bound, as a request for the project would read them. A request handler never sets
    the bypass flag. The binding the transaction had before is put back at the end. An
    organization has a handful of projects, so one small query per project is cheap.
    """
    org_id = await db.scalar(
        select(ProviderCredential.org_id).where(ProviderCredential.id == credential_id)
    )
    if org_id is None:
        return None
    project_ids = (
        await db.scalars(select(Project.id).where(Project.org_id == org_id).order_by(Project.id))
    ).all()
    previous = await db.scalar(text("SELECT current_setting('app.project_id', true)"))
    targets_credential = GatewayRoute.config.contains(
        {"targets": [{"credential_id": str(credential_id)}]}
    )
    route_name: str | None = None
    for project_id in project_ids:
        await bind_project(db, project_id)
        route_name = await db.scalar(
            select(GatewayRoute.name)
            .where(GatewayRoute.project_id == project_id, targets_credential)
            .order_by(GatewayRoute.name)
            .limit(1)
        )
        if route_name is not None:
            break
    await db.execute(
        text("SELECT set_config('app.project_id', :previous, true)"),
        {"previous": previous or ""},
    )
    return route_name


async def share_lock_credentials(
    db: AsyncSession, org_id: uuid.UUID, credential_ids: Sequence[uuid.UUID]
) -> set[uuid.UUID]:
    """Which of the credentials belong to the organization, key-share locked until commit.

    The lock conflicts with the one `delete_credential` takes, so a route is never saved with a
    credential that a concurrent deletion is removing: whichever commits second sees the other.
    """
    if not credential_ids:
        return set()
    found = await db.scalars(
        select(ProviderCredential.id)
        .where(ProviderCredential.org_id == org_id, ProviderCredential.id.in_(credential_ids))
        .with_for_update(key_share=True)
    )
    return set(found.all())


async def credentials_not_sealed_under(
    db: AsyncSession, active_key_id: str, *, exclude: Sequence[uuid.UUID], limit: int
) -> Sequence[ProviderCredential]:
    """Up to `limit` credentials, across every organization, sealed under another key.

    For the reseal command, which runs as an operator and is the one caller allowed to read
    across organizations. The rows are locked, and a row another transaction holds (a rotation,
    another reseal) is waited for rather than skipped: a skipped row would keep needing the old
    key while the command reported success.
    """
    statement = (
        select(ProviderCredential)
        .where(ProviderCredential.key_id != active_key_id)
        .order_by(ProviderCredential.id)
        .limit(limit)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if exclude:
        statement = statement.where(ProviderCredential.id.not_in(exclude))
    return (await db.scalars(statement)).all()


async def count_credentials_not_sealed_under(db: AsyncSession, active_key_id: str) -> int:
    """How many credentials, across every organization, need a key other than the active one."""
    count = await db.scalar(
        select(func.count())
        .select_from(ProviderCredential)
        .where(ProviderCredential.key_id != active_key_id)
    )
    return int(count or 0)
