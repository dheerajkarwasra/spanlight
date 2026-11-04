"""Gateway routes: which credentials serve a project's gateway calls, and how failures are handled.

Members read routes and their history (`project:read`); owners and admins change them
(`gateway:write`). Every save is a new version: an edit names the version it started from and
is `409 ROUTE_VERSION_CONFLICT` when someone saved in between, and a revert saves an old config
again as the next version.
"""

import uuid
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Request, status

from app.api.deps import Access, ClockDep, DbSession, client_ip, require
from app.api.locks import lock_project_of
from app.api.schemas import (
    RouteCreateIn,
    RouteOut,
    RouteRevertIn,
    RouteUpdateIn,
    RouteVersionOut,
    UserOut,
)
from app.core.errors import ProblemError, conflict, not_found
from app.core.permissions import Permission
from app.db.models import GatewayRoute, User
from app.gateway import route_queries, route_service
from app.gateway.route_config import load_stored
from app.gateway.route_errors import (
    InvalidRouteConfigError,
    RouteInUseError,
    RouteNameTakenError,
    RouteNotFoundError,
    RouteVersionConflictError,
    RouteVersionNotFoundError,
)

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/projects/{project_id}/gateway/routes", tags=["gateway"])

RouteReader = Annotated[Access, Depends(require(Permission.PROJECT_READ))]
RouteWriter = Annotated[Access, Depends(require(Permission.GATEWAY_WRITE))]

ROUTE_ERRORS = (
    RouteNotFoundError,
    RouteVersionNotFoundError,
    RouteNameTakenError,
    RouteVersionConflictError,
    RouteInUseError,
    InvalidRouteConfigError,
)


def _user_out(user: User | None) -> UserOut | None:
    return UserOut.model_validate(user) if user else None


def _route_out(route: GatewayRoute, updater: User | None) -> RouteOut:
    return RouteOut(
        id=route.id,
        name=route.name,
        is_default=route.is_default,
        config=load_stored(route.config),
        version=route.version,
        updated_by=_user_out(updater),
        updated_at=route.updated_at,
    )


def _route_problem(error: Exception) -> ProblemError:
    """The problem response for an error the route service raises."""
    if isinstance(error, RouteNotFoundError):
        return not_found()
    if isinstance(error, RouteVersionNotFoundError):
        return not_found(f"This route has no version {error.version}.")
    if isinstance(error, RouteNameTakenError):
        return conflict(
            "ROUTE_NAME_TAKEN", "A route with this name already exists in this project."
        )
    if isinstance(error, RouteVersionConflictError):
        return ProblemError(
            409,
            "ROUTE_VERSION_CONFLICT",
            f"Someone saved version {error.current_version} while you were editing. "
            "Reload the route to see their changes.",
            extensions={"current_version": error.current_version},
        )
    if isinstance(error, RouteInUseError):
        return conflict(
            "ROUTE_IN_USE",
            f"{error.route_name} is used by gateway key {error.key_name}. "
            "Move the key to another route first.",
        )
    if isinstance(error, InvalidRouteConfigError):
        return ProblemError(422, "VALIDATION_ERROR", "The request is invalid.", errors=error.errors)
    raise error


async def _found_route(db: DbSession, project_id: uuid.UUID, route_id: uuid.UUID) -> RouteOut:
    found = await route_queries.get_route_with_updater(db, project_id, route_id)
    if found is None:
        raise not_found()
    return _route_out(*found)


def _log(event: str, access: Access, route_id: uuid.UUID, **fields: object) -> None:
    project_id = str(access.require_project().id)
    logger.info(
        event, org_id=str(access.org.id), project_id=project_id, route_id=str(route_id), **fields
    )


@router.get("", response_model=list[RouteOut], summary="List gateway routes")
async def list_routes(project_id: uuid.UUID, access: RouteReader, db: DbSession) -> list[RouteOut]:
    """By name. Unpaginated: a project keeps a handful of routes."""
    rows = await route_queries.list_routes(db, access.require_project().id)
    return [_route_out(route, updater) for route, updater in rows]


@router.post(
    "", status_code=status.HTTP_201_CREATED, response_model=RouteOut, summary="Create a route"
)
async def create_route(
    project_id: uuid.UUID,
    body: RouteCreateIn,
    request: Request,
    access: RouteWriter,
    db: DbSession,
    clock: ClockDep,
) -> RouteOut:
    """Save the route as version 1. The project's first route becomes its default.

    `422` on `config.targets.<i>.credential_id` for a credential that is not the organization's;
    `409 ROUTE_NAME_TAKEN` for a name already used in the project.
    """
    locked_project_id = await lock_project_of(access, db)
    try:
        route = await route_service.create_route(
            db,
            access.org.id,
            locked_project_id,
            access.user_id,
            body.name,
            body.config,
            now=clock(),
            ip=client_ip(request),
        )
    except ROUTE_ERRORS as error:
        raise _route_problem(error) from None
    await db.commit()
    _log("gateway_route_created", access, route.id, version=route.version)
    return _route_out(route, access.auth.user)


@router.get("/{route_id}", response_model=RouteOut, summary="Get a route")
async def get_route(
    project_id: uuid.UUID, route_id: uuid.UUID, access: RouteReader, db: DbSession
) -> RouteOut:
    return await _found_route(db, access.require_project().id, route_id)


@router.put("/{route_id}", response_model=RouteOut, summary="Save a new version of a route")
async def update_route(
    project_id: uuid.UUID,
    route_id: uuid.UUID,
    body: RouteUpdateIn,
    request: Request,
    access: RouteWriter,
    db: DbSession,
    clock: ClockDep,
) -> RouteOut:
    """Replace the config, if the route is still at `expected_version`; bumps `version`.

    `409 ROUTE_VERSION_CONFLICT` when another save came first: reload and apply the change again.
    """
    locked_project_id = await lock_project_of(access, db)
    try:
        route = await route_service.update_route(
            db,
            access.org.id,
            locked_project_id,
            route_id,
            access.user_id,
            body.config,
            body.expected_version,
            now=clock(),
            ip=client_ip(request),
        )
    except ROUTE_ERRORS as error:
        raise _route_problem(error) from None
    await db.commit()
    # This process's gateway calls read the change from the next call, not after the key cache TTL.
    request.app.state.gateway_key_cache.clear()
    _log("gateway_route_updated", access, route.id, version=route.version)
    return _route_out(route, access.auth.user)


@router.get(
    "/{route_id}/versions", response_model=list[RouteVersionOut], summary="List a route's versions"
)
async def list_route_versions(
    project_id: uuid.UUID, route_id: uuid.UUID, access: RouteReader, db: DbSession
) -> list[RouteVersionOut]:
    """Every saved config, newest first. `404` for an unknown route."""
    project = access.require_project()
    if await route_queries.route_version(db, project.id, route_id) is None:
        raise not_found()
    rows = await route_queries.list_route_versions(db, project.id, route_id)
    return [
        RouteVersionOut(
            version=saved.version,
            config=load_stored(saved.config),
            updated_by=_user_out(updater),
            created_at=saved.created_at,
        )
        for saved, updater in rows
    ]


@router.post("/{route_id}/revert", response_model=RouteOut, summary="Revert a route")
async def revert_route(
    project_id: uuid.UUID,
    route_id: uuid.UUID,
    body: RouteRevertIn,
    request: Request,
    access: RouteWriter,
    db: DbSession,
    clock: ClockDep,
) -> RouteOut:
    """Save the config of `version` again as a new version; the history is never rewritten.

    `404` for a version the route never had; `422` when that config names a credential deleted
    since.
    """
    locked_project_id = await lock_project_of(access, db)
    try:
        route = await route_service.revert_route(
            db,
            access.org.id,
            locked_project_id,
            route_id,
            access.user_id,
            body.version,
            now=clock(),
            ip=client_ip(request),
        )
    except ROUTE_ERRORS as error:
        raise _route_problem(error) from None
    await db.commit()
    request.app.state.gateway_key_cache.clear()
    _log(
        "gateway_route_reverted", access, route.id, version=route.version, from_version=body.version
    )
    return _route_out(route, access.auth.user)


@router.post("/{route_id}/default", response_model=RouteOut, summary="Make a route the default")
async def set_default_route(
    project_id: uuid.UUID,
    route_id: uuid.UUID,
    request: Request,
    access: RouteWriter,
    db: DbSession,
) -> RouteOut:
    """Gateway keys created without a route use the default one. Not a new version."""
    locked_project_id = await lock_project_of(access, db)
    try:
        await route_service.set_default_route(
            db, access.org.id, locked_project_id, route_id, access.user_id, ip=client_ip(request)
        )
    except ROUTE_ERRORS as error:
        raise _route_problem(error) from None
    # Read before the commit: it ends the transaction, and with it the project binding that
    # row-level security needs to show the route.
    route = await _found_route(db, locked_project_id, route_id)
    await db.commit()
    _log("gateway_route_made_default", access, route_id)
    return route


@router.delete("/{route_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a route")
async def delete_route(
    project_id: uuid.UUID,
    route_id: uuid.UUID,
    request: Request,
    access: RouteWriter,
    db: DbSession,
) -> None:
    """Delete the route and its history. `409 ROUTE_IN_USE` while a gateway key uses it."""
    locked_project_id = await lock_project_of(access, db)
    try:
        await route_service.delete_route(
            db, access.org.id, locked_project_id, route_id, access.user_id, ip=client_ip(request)
        )
    except ROUTE_ERRORS as error:
        raise _route_problem(error) from None
    await db.commit()
    _log("gateway_route_deleted", access, route_id)
