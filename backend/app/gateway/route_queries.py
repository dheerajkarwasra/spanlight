"""Reads of gateway routes and their saved versions. Nothing in this module writes.

Routes are project-scoped and under row-level security; each query also names the project, so a
route id from another project finds nothing even before the policy applies.
"""

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import GatewayKey, GatewayRoute, GatewayRouteVersion, User


async def list_routes(
    db: AsyncSession, project_id: uuid.UUID
) -> Sequence[tuple[GatewayRoute, User | None]]:
    """The project's routes with the user who last saved each, by name.

    Unpaginated: a project keeps a handful of routes.
    """
    rows = await db.execute(
        select(GatewayRoute, User)
        .outerjoin(User, User.id == GatewayRoute.updated_by)
        .where(GatewayRoute.project_id == project_id)
        .order_by(GatewayRoute.name, GatewayRoute.id)
    )
    return [(route, updater) for route, updater in rows]


async def get_route(
    db: AsyncSession, project_id: uuid.UUID, route_id: uuid.UUID
) -> GatewayRoute | None:
    return await db.scalar(
        select(GatewayRoute).where(
            GatewayRoute.project_id == project_id, GatewayRoute.id == route_id
        )
    )


async def get_route_with_updater(
    db: AsyncSession, project_id: uuid.UUID, route_id: uuid.UUID
) -> tuple[GatewayRoute, User | None] | None:
    row = (
        await db.execute(
            select(GatewayRoute, User)
            .outerjoin(User, User.id == GatewayRoute.updated_by)
            .where(GatewayRoute.project_id == project_id, GatewayRoute.id == route_id)
        )
    ).one_or_none()
    if row is None:
        return None
    route, updater = row.tuple()
    return route, updater


async def lock_route(
    db: AsyncSession, project_id: uuid.UUID, route_id: uuid.UUID
) -> GatewayRoute | None:
    """The route, locked until the transaction ends.

    The lock conflicts with the key-share lock a new reference to the route takes, so deleting
    the route and pointing a gateway key at it do not interleave.
    """
    return await db.scalar(
        select(GatewayRoute)
        .where(GatewayRoute.project_id == project_id, GatewayRoute.id == route_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


async def route_version(db: AsyncSession, project_id: uuid.UUID, route_id: uuid.UUID) -> int | None:
    """The route's current version, or None when there is no such route."""
    return await db.scalar(
        select(GatewayRoute.version).where(
            GatewayRoute.project_id == project_id, GatewayRoute.id == route_id
        )
    )


async def project_has_routes(db: AsyncSession, project_id: uuid.UUID) -> bool:
    found = await db.scalar(
        select(GatewayRoute.id).where(GatewayRoute.project_id == project_id).limit(1)
    )
    return found is not None


async def lock_project_routes(db: AsyncSession, project_id: uuid.UUID) -> Sequence[uuid.UUID]:
    """Lock every route of the project, in id order, until the transaction ends.

    Moving the default touches two routes; locking them all first makes two concurrent moves
    wait for each other instead of both clearing the old default and colliding on the new one.
    """
    return (
        await db.scalars(
            select(GatewayRoute.id)
            .where(GatewayRoute.project_id == project_id)
            .order_by(GatewayRoute.id)
            .with_for_update()
        )
    ).all()


async def list_route_versions(
    db: AsyncSession, project_id: uuid.UUID, route_id: uuid.UUID
) -> Sequence[tuple[GatewayRouteVersion, User | None]]:
    """Every saved config of the route with the user who saved it, newest first.

    Unpaginated: a version is one save by a person, so a route collects tens, not millions.
    """
    rows = await db.execute(
        select(GatewayRouteVersion, User)
        .outerjoin(User, User.id == GatewayRouteVersion.updated_by)
        .where(
            GatewayRouteVersion.project_id == project_id,
            GatewayRouteVersion.route_id == route_id,
        )
        .order_by(GatewayRouteVersion.version.desc())
    )
    return [(version, updater) for version, updater in rows]


async def get_route_version(
    db: AsyncSession, project_id: uuid.UUID, route_id: uuid.UUID, version: int
) -> GatewayRouteVersion | None:
    return await db.scalar(
        select(GatewayRouteVersion).where(
            GatewayRouteVersion.project_id == project_id,
            GatewayRouteVersion.route_id == route_id,
            GatewayRouteVersion.version == version,
        )
    )


async def route_in_use(db: AsyncSession, route_id: uuid.UUID) -> str | None:
    """The name of an active gateway key that sends its calls through the route, or None.

    A revoked key does not count: it sends no calls, and deleting the route clears its
    reference (`key_service.release_route`).
    """
    return await db.scalar(
        select(GatewayKey.name)
        .where(GatewayKey.route_id == route_id, GatewayKey.revoked_at.is_(None))
        .order_by(GatewayKey.name, GatewayKey.id)
        .limit(1)
    )
