"""Gateway keys: what an application sends to the LLM gateway, and the limits that apply to it.

Members list keys (`project:read`); owners and admins create, edit and revoke them
(`gateway:write`). The full key is in the create response only, which no cache may keep;
afterwards only its prefix is shown. Revoking is final: the gateway refuses the key from the
next call, and the key stays listed with `revoked_at` set.
"""

import uuid
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Request, Response, status

from app.api.deps import (
    Access,
    ClockDep,
    CurrentSession,
    DbSession,
    SettingsDep,
    client_ip,
    raise_session_required,
    require,
)
from app.api.locks import lock_project_of
from app.api.principals import SessionPrincipal
from app.api.schemas import (
    GatewayKeyCreate,
    GatewayKeyCreatedOut,
    GatewayKeyOut,
    GatewayKeyUpdate,
    UserOut,
)
from app.auth.reauth import confirm_account_owner
from app.core.errors import FieldError, ProblemError, not_found
from app.core.permissions import Permission
from app.core.security import mark_uncacheable
from app.db.models import GatewayKey, User
from app.gateway import key_queries, key_service
from app.gateway.key_errors import (
    FaultProfileOnProductionKeyError,
    KeyFaultProfileNotFoundError,
    KeyNotFoundError,
    KeyRouteNotFoundError,
    NoDefaultRouteError,
)
from app.gateway.key_schemas import widens

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/projects/{project_id}/gateway/keys", tags=["gateway"])

KeyReader = Annotated[Access, Depends(require(Permission.PROJECT_READ))]
KeyWriter = Annotated[Access, Depends(require(Permission.GATEWAY_WRITE))]

KEY_ERRORS = (
    KeyNotFoundError,
    KeyRouteNotFoundError,
    NoDefaultRouteError,
    KeyFaultProfileNotFoundError,
    FaultProfileOnProductionKeyError,
)


def _key_out(key: GatewayKey, creator: User | None, creator_is_member: bool) -> GatewayKeyOut:
    return GatewayKeyOut(
        creator_is_member=creator_is_member,
        id=key.id,
        name=key.name,
        prefix=key.prefix,
        route_id=key.route_id,
        environment=key.environment,
        rpm_limit=key.rpm_limit,
        tpm_limit=key.tpm_limit,
        allowed_models=list(key.allowed_models),
        default_tags=list(key.default_tags),
        cache_ttl_seconds=key.cache_ttl_seconds,
        fault_profile_id=key.fault_profile_id,
        created_by=UserOut.model_validate(creator) if creator else None,
        created_at=key.created_at,
        last_used_at=key.last_used_at,
        revoked_at=key.revoked_at,
    )


def _key_problem(error: Exception) -> ProblemError:
    """The problem response for an error the key service raises."""
    if isinstance(error, KeyNotFoundError):
        return not_found()
    if isinstance(error, KeyRouteNotFoundError):
        return ProblemError(
            422,
            "VALIDATION_ERROR",
            "The request is invalid.",
            errors=[FieldError(field="route_id", message="is not a route of this project")],
        )
    if isinstance(error, KeyFaultProfileNotFoundError):
        return ProblemError(
            422,
            "VALIDATION_ERROR",
            "The request is invalid.",
            errors=[
                FieldError(
                    field="fault_profile_id", message="is not a fault profile of this project"
                )
            ],
        )
    if isinstance(error, FaultProfileOnProductionKeyError):
        return ProblemError(
            422,
            "FAULT_PROFILE_ON_PRODUCTION_KEY",
            "Fault profiles cannot attach to production keys. Lab faults only run on keys from "
            "other environments.",
        )
    if isinstance(error, NoDefaultRouteError):
        return ProblemError(
            422,
            "NO_DEFAULT_ROUTE",
            "This project has no default route. Choose a route for the key, or make one of "
            "the project's routes the default.",
        )
    raise error


def _log(event: str, access: Access, key: GatewayKey, **fields: object) -> None:
    logger.info(
        event,
        org_id=str(access.org.id),
        project_id=str(key.project_id),
        gateway_key_id=str(key.id),
        prefix=key.prefix,
        **fields,
    )


@router.get("", response_model=list[GatewayKeyOut], summary="List gateway keys")
async def list_keys(project_id: uuid.UUID, access: KeyReader, db: DbSession) -> list[GatewayKeyOut]:
    """Newest first, revoked keys included. Unpaginated: a project keeps a handful of keys."""
    rows = await key_queries.list_keys(db, access.require_project().id)
    return [_key_out(*row) for row in rows]


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=GatewayKeyCreatedOut,
    summary="Create a gateway key",
)
async def create_key(
    project_id: uuid.UUID,
    body: GatewayKeyCreate,
    request: Request,
    response: Response,
    auth: CurrentSession,
    access: KeyWriter,
    db: DbSession,
    clock: ClockDep,
    settings: SettingsDep,
) -> GatewayKeyCreatedOut:
    """Create the key and return it with its `secret`, which is never shown again.

    Without `route_id` the key uses the project's default route: `422 NO_DEFAULT_ROUTE` when the
    project has none, and `422` on `route_id` for a route that is not the project's.
    Requires a session and account-owner proof: `password` (or a sign-in within 10 minutes
    for passwordless accounts), plus `code` when two-factor authentication is enabled.
    Tokens get `403 SESSION_REQUIRED`. Route validation precedes proof; existing keys keep working.
    """
    if not await key_queries.precheck_route(db, access.require_project().id, body.route_id):
        error = NoDefaultRouteError() if body.route_id is None else KeyRouteNotFoundError()
        raise _key_problem(error)
    await confirm_account_owner(
        db,
        auth,
        body.password,
        body.code,
        scope="gateway_key_reauth",
        settings=settings,
        now=clock(),
    )
    locked_project_id = await lock_project_of(access, db)
    try:
        key, secret = await key_service.create_key(
            db,
            access.org.id,
            locked_project_id,
            access.user_id,
            body,
            now=clock(),
            ip=client_ip(request),
        )
    except KEY_ERRORS as error:
        await db.commit()
        raise _key_problem(error) from None
    # Built before the commit, which ends the project binding that row-level security needs.
    created = GatewayKeyCreatedOut(
        **_key_out(key, access.auth.user, True).model_dump(), secret=secret
    )
    await db.commit()
    _log("gateway_key_created", access, key)
    mark_uncacheable(response)  # the secret is shown once
    return created


@router.patch("/{key_id}", response_model=GatewayKeyOut, summary="Edit a gateway key")
async def update_key(
    project_id: uuid.UUID,
    key_id: uuid.UUID,
    body: GatewayKeyUpdate,
    request: Request,
    access: KeyWriter,
    db: DbSession,
    settings: SettingsDep,
    clock: ClockDep,
) -> GatewayKeyOut:
    """Change the fields sent; send `null` to clear a limit or the cache TTL.

    `404` for a revoked key: it can no longer be edited.
    Widening requires a session and owner proof (`password` and, when enabled, `code`):
    changing route or environment, raising or clearing limits, or expanding allowed models.
    Tokens may still tighten, rename, tag, change cache TTL or attach/detach a fault profile.
    A concurrent edit that makes an unconfirmed session patch widen returns `409 KEY_CHANGED`.
    """
    found = await key_queries.get_key_with_creator(db, access.require_project().id, key_id)
    if found is None or found[0].revoked_at is not None:
        raise not_found()
    confirmed = widens(found[0], body.changes())
    if confirmed:
        if not isinstance(access.auth, SessionPrincipal):
            raise_session_required()
        await confirm_account_owner(
            db,
            access.auth,
            body.password,
            body.code,
            scope="gateway_key_reauth",
            settings=settings,
            now=clock(),
        )
    locked_project_id = await lock_project_of(access, db)
    locked_key = await key_queries.lock_key(db, locked_project_id, key_id)
    # A concurrent tightening can turn this request into a widening after the first read.
    if (
        locked_key is not None
        and locked_key.revoked_at is None
        and not confirmed
        and widens(locked_key, body.changes())
    ):
        if not isinstance(access.auth, SessionPrincipal):
            raise_session_required()
        raise ProblemError(409, "KEY_CHANGED", "The key changed. Reload it and try again.")
    try:
        key = await key_service.update_key(
            db,
            access.org.id,
            locked_project_id,
            key_id,
            access.user_id,
            body,
            ip=client_ip(request),
        )
    except KEY_ERRORS as error:
        if confirmed:
            await db.commit()
        raise _key_problem(error) from None
    found = await key_queries.get_key_with_creator(db, locked_project_id, key.id)
    if found is None:
        raise not_found()
    updated = _key_out(*found)
    await db.commit()
    # This process's gateway calls read the change from the next call, not after the key cache TTL.
    request.app.state.gateway_key_cache.clear()
    _log("gateway_key_updated", access, key, fields=sorted(body.changes()))
    return updated


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Revoke a gateway key")
async def revoke_key(
    project_id: uuid.UUID,
    key_id: uuid.UUID,
    request: Request,
    access: KeyWriter,
    db: DbSession,
    clock: ClockDep,
) -> None:
    """Revoke the key. Apps using it get `401` from the next call to this api process, and within
    `API_KEY_CACHE_TTL_SECONDS` (10 s by default) on other processes. Revoking twice is a no-op."""
    locked_project_id = await lock_project_of(access, db)
    try:
        await key_service.revoke_key(
            db,
            access.org.id,
            locked_project_id,
            key_id,
            access.user_id,
            now=clock(),
            ip=client_ip(request),
        )
    except KEY_ERRORS as error:
        raise _key_problem(error) from None
    await db.commit()
    request.app.state.gateway_key_cache.clear()
    logger.info(
        "gateway_key_revoked",
        org_id=str(access.org.id),
        project_id=str(locked_project_id),
        gateway_key_id=str(key_id),
    )
