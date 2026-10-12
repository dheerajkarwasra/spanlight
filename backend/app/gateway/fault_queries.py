"""Reads of fault profiles and of the keys that run them. Nothing here writes.

Profiles are project-scoped and under row-level security; each query also names the project, so
a profile id from another project finds nothing even before the policy applies.
"""

import uuid
from collections import defaultdict
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import FaultProfile, GatewayKey, User


async def list_profiles(
    db: AsyncSession, project_id: uuid.UUID
) -> Sequence[tuple[FaultProfile, User | None]]:
    """The project's profiles with who created each, by name.

    Unpaginated: a lab keeps a handful of profiles.
    """
    rows = await db.execute(
        select(FaultProfile, User)
        .outerjoin(User, User.id == FaultProfile.created_by)
        .where(FaultProfile.project_id == project_id)
        .order_by(FaultProfile.name, FaultProfile.id)
    )
    return [(profile, creator) for profile, creator in rows]


async def get_profile_with_creator(
    db: AsyncSession, project_id: uuid.UUID, profile_id: uuid.UUID
) -> tuple[FaultProfile, User | None] | None:
    row = (
        await db.execute(
            select(FaultProfile, User)
            .outerjoin(User, User.id == FaultProfile.created_by)
            .where(FaultProfile.project_id == project_id, FaultProfile.id == profile_id)
        )
    ).one_or_none()
    if row is None:
        return None
    profile, creator = row.tuple()
    return profile, creator


async def lock_profile(
    db: AsyncSession, project_id: uuid.UUID, profile_id: uuid.UUID, *, exclusive: bool = True
) -> FaultProfile | None:
    """The profile, locked until the transaction ends.

    `exclusive` (for a delete) conflicts with the key-share lock a key takes to run the profile
    (`key_queries.share_lock_fault_profile`), so deleting the profile and attaching it do not
    interleave. An edit leaves `exclusive` off: it takes the weaker lock that serializes edits
    but lets keys attach meanwhile, since it never changes the profile's id.
    """
    return await db.scalar(
        select(FaultProfile)
        .where(FaultProfile.project_id == project_id, FaultProfile.id == profile_id)
        .with_for_update(key_share=not exclusive)
        .execution_options(populate_existing=True)
    )


async def attached_key_ids(
    db: AsyncSession, project_id: uuid.UUID
) -> dict[uuid.UUID, list[uuid.UUID]]:
    """The ids of the active (not revoked) keys that run each profile, newest key first.

    Profiles no key runs are absent from the result.
    """
    rows = await db.execute(
        select(GatewayKey.fault_profile_id, GatewayKey.id)
        .where(
            GatewayKey.project_id == project_id,
            GatewayKey.fault_profile_id.is_not(None),
            GatewayKey.revoked_at.is_(None),
        )
        .order_by(GatewayKey.created_at.desc(), GatewayKey.id.desc())
    )
    attached: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    for profile_id, key_id in rows:
        if profile_id is not None:
            attached[profile_id].append(key_id)
    return attached
