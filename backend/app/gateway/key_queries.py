"""Reads of gateway keys and of the routes a key may point at. Nothing in this module writes.

Keys are project-scoped and under row-level security; each query also names the project, so a
key id from another project finds nothing even before the policy applies. Finding a key by its
prefix, before the project is known, is `key_context.find_key`'s job, not this module's.
"""

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import FaultProfile, GatewayKey, GatewayKeyMinute, GatewayRoute, User


async def list_keys(
    db: AsyncSession, project_id: uuid.UUID
) -> Sequence[tuple[GatewayKey, User | None]]:
    """The project's keys, revoked ones included, with who created each, newest first.

    Unpaginated: a project keeps a key per application and environment, not thousands.
    """
    rows = await db.execute(
        select(GatewayKey, User)
        .outerjoin(User, User.id == GatewayKey.created_by)
        .where(GatewayKey.project_id == project_id)
        .order_by(GatewayKey.created_at.desc(), GatewayKey.id.desc())
    )
    return [(key, creator) for key, creator in rows]


async def get_key_with_creator(
    db: AsyncSession, project_id: uuid.UUID, key_id: uuid.UUID
) -> tuple[GatewayKey, User | None] | None:
    row = (
        await db.execute(
            select(GatewayKey, User)
            .outerjoin(User, User.id == GatewayKey.created_by)
            .where(GatewayKey.project_id == project_id, GatewayKey.id == key_id)
        )
    ).one_or_none()
    if row is None:
        return None
    key, creator = row
    return key, creator


async def lock_key(db: AsyncSession, project_id: uuid.UUID, key_id: uuid.UUID) -> GatewayKey | None:
    """The key, locked until the transaction ends, so two edits or revocations do not interleave."""
    return await db.scalar(
        select(GatewayKey)
        .where(GatewayKey.project_id == project_id, GatewayKey.id == key_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


async def share_lock_route(db: AsyncSession, project_id: uuid.UUID, route_id: uuid.UUID) -> bool:
    """Key-share lock the route for a key that will point at it; False if it is not the project's.

    The lock conflicts with the one `route_service.delete_route` takes, so a key is never pointed
    at a route that a concurrent deletion is removing: whichever commits second sees the other.
    """
    found = await db.scalar(
        select(GatewayRoute.id)
        .where(GatewayRoute.project_id == project_id, GatewayRoute.id == route_id)
        .with_for_update(key_share=True)
    )
    return found is not None


async def share_lock_fault_profile(
    db: AsyncSession, project_id: uuid.UUID, profile_id: uuid.UUID
) -> bool:
    """Key-share lock the fault profile for a key that will run it; False if not the project's.

    The lock conflicts with the one `fault_service.delete_profile` takes, as for routes.
    """
    found = await db.scalar(
        select(FaultProfile.id)
        .where(FaultProfile.project_id == project_id, FaultProfile.id == profile_id)
        .with_for_update(key_share=True)
    )
    return found is not None


async def share_lock_default_route(db: AsyncSession, project_id: uuid.UUID) -> uuid.UUID | None:
    """The project's default route, key-share locked like `share_lock_route`; None if none."""
    return await db.scalar(
        select(GatewayRoute.id)
        .where(GatewayRoute.project_id == project_id, GatewayRoute.is_default)
        .with_for_update(key_share=True)
    )


async def minute_tokens(db: AsyncSession, key_id: uuid.UUID, minute_start: datetime) -> int | None:
    """The tokens the key has recorded in the minute starting at `minute_start`; None if none."""
    return await db.scalar(
        select(GatewayKeyMinute.tokens).where(
            GatewayKeyMinute.key_id == key_id, GatewayKeyMinute.minute_start == minute_start
        )
    )
