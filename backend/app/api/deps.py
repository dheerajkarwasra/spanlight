"""Shared FastAPI dependencies: database session, authentication, authorization."""

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal, overload

from fastapi import Depends, Request
from fastapi.params import Depends as DependsMarker
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.principals import (
    ApiKeyPrincipal,
    Principal,
    SessionPrincipal,
    TokenPrincipal,
    UserPrincipal,
)
from app.auth.tokens import find_presented_token, parse_token
from app.config import Settings
from app.core.errors import (
    ProblemError,
    forbidden,
    key_expired,
    key_scope,
    not_found,
    session_required,
    token_expired,
    token_scope,
    unauthorized,
)
from app.core.permissions import Permission, has, permission_class
from app.core.ratelimit import API_READ_LIMITER, enforce_rate_limit
from app.core.scopes import KeyScope
from app.core.security import (
    API_KEY_PREFIX,
    PAT_PREFIX,
    is_bearer,
    key_secret_matches,
    parse_key,
    verify_csrf_token,
)
from app.db.models import (
    ApiKey,
    Membership,
    MembershipRole,
    Organization,
    PersonalAccessToken,
    Project,
    TokenScope,
)
from app.db.rls import bind_project
from app.db.timeouts import limit_statement_time
from app.services.sessions import resolve_session

SESSION_COOKIE = "spl_session"
CSRF_COOKIE = "spl_csrf"
CSRF_HEADER = "x-csrf-token"
_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_READ_METHODS = frozenset({"GET", "HEAD"})
LAST_USED_RESOLUTION = timedelta(minutes=1)


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    """One session (and transaction) per request. Handlers commit explicitly.

    A statement that runs longer than `API_STATEMENT_TIMEOUT_SECONDS` is cancelled and the request
    answers 503 (see `app.core.errors`), so one slow read cannot hold a connection indefinitely.
    """
    settings: Settings = request.app.state.settings
    async with request.app.state.session_factory() as session:
        limit_statement_time(session, round(settings.api_statement_timeout_seconds * 1000))
        yield session


SettingsDep = Annotated[Settings, Depends(get_settings)]


Clock = Callable[[], datetime]


def get_clock(request: Request) -> Clock:
    """The time source for one-time codes, login challenges and the other `/api/v1` reads that
    take a `now` (the budgets API among them); the gateway request path reads the same clock.
    The wall clock, except in tests."""
    clock: Clock = request.app.state.clock
    return clock


ClockDep = Annotated[Clock, Depends(get_clock)]
DbSession = Annotated[AsyncSession, Depends(get_db)]


def client_ip(request: Request) -> str | None:
    # Behind a proxy, run uvicorn with --proxy-headers so this is the real client.
    return request.client.host if request.client else None


def utcnow() -> datetime:
    return datetime.now(UTC)


async def authenticate_session(
    request: Request, db: AsyncSession, settings: Settings
) -> SessionPrincipal:
    """Authenticate the session cookie; enforce CSRF on state-changing methods."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise unauthorized()
    active = await resolve_session(db, token, now=utcnow())
    if active is None:
        raise unauthorized("Your session has expired. Please sign in again.")

    if request.method in _UNSAFE_METHODS:
        header_token = request.headers.get(CSRF_HEADER)
        cookie_token = request.cookies.get(CSRF_COOKIE)
        if (
            not header_token
            or header_token != cookie_token
            or not verify_csrf_token(settings.secret_key.get_secret_value(), token, header_token)
        ):
            raise ProblemError(403, "CSRF_FAILED", "Missing or invalid CSRF token.")
    return SessionPrincipal(user=active.user, session=active.session)


def _invalid_credentials() -> ProblemError:
    return unauthorized("Missing, invalid or revoked API key.")


def _invalid_token() -> ProblemError:
    return unauthorized("Missing, invalid or revoked access token.")


def _bearer_credentials(request: Request) -> str:
    """What follows `Bearer` in the `Authorization` header, or "" if there is no such header."""
    authorization = request.headers.get("authorization", "")
    if not is_bearer(authorization):
        return ""
    _, _, credentials = authorization.strip().partition(" ")
    return credentials.strip()


async def authenticate_bearer(
    request: Request, db: AsyncSession
) -> ApiKeyPrincipal | TokenPrincipal:
    """Authenticate `Authorization: Bearer …` as a personal access token or a project API key.

    The credential's prefix says which: `spl_pat_…` is a token, anything else is read as an API
    key. A missing, malformed, unknown or revoked credential is 401, and so is an expired one;
    a request that carries a bearer is never authenticated by its cookie instead. May commit
    (see `_record_use`), so it runs before anything transaction-local such as the RLS binding.
    """
    credentials = _bearer_credentials(request)
    if credentials.startswith(PAT_PREFIX):
        return await _authenticate_token(db, credentials)
    return await _authenticate_api_key(db, credentials)


async def authenticate_api_key(request: Request, db: AsyncSession) -> ApiKeyPrincipal:
    """Authenticate a bearer as a project API key, and only as one.

    For the ingestion routes, which a personal access token has no business on: a token, valid
    or not, gets the same 401 as any credential that is not an API key.
    """
    return await _authenticate_api_key(db, _bearer_credentials(request))


async def _authenticate_api_key(db: AsyncSession, credentials: str) -> ApiKeyPrincipal:
    parsed = parse_key(credentials, API_KEY_PREFIX)
    if parsed is None:
        raise _invalid_credentials()

    key = await db.scalar(select(ApiKey).where(ApiKey.prefix == parsed.prefix))
    # The secret comes first, so only the holder of a key can learn that it was revoked or
    # expired; anyone else gets the same answer as for a key that never existed.
    if key is None or not key_secret_matches(parsed.secret, key.secret_hash):
        raise _invalid_credentials()
    if key.revoked_at is not None:
        raise _invalid_credentials()
    now = utcnow()
    if key.expires_at is not None and key.expires_at <= now:
        raise key_expired()

    await _record_use(db, ApiKey, key.id, now)
    return ApiKeyPrincipal(
        key_id=key.id,
        project_id=key.project_id,
        scopes=frozenset(KeyScope(scope) for scope in key.scopes),
    )


async def _authenticate_token(db: AsyncSession, credentials: str) -> TokenPrincipal:
    parsed = parse_token(credentials)
    if parsed is None:
        raise _invalid_token()

    # As for a key, the secret comes first: a wrong one is the same 401 whatever the token's state.
    presented = await find_presented_token(db, parsed)
    if presented is None or presented.token.revoked_at is not None:
        raise _invalid_token()
    token = presented.token
    now = utcnow()
    if token.expires_at is not None and token.expires_at <= now:
        raise token_expired()

    await _record_use(db, PersonalAccessToken, token.id, now)
    return TokenPrincipal(user=presented.user, token_id=token.id, scope=token.scope)


async def _record_use(
    db: AsyncSession,
    model: type[ApiKey] | type[PersonalAccessToken],
    row_id: uuid.UUID,
    now: datetime,
) -> None:
    """Record that a key or token was used, at most once a minute.

    A busy credential must not write on every request. The condition is part of the UPDATE, so
    concurrent requests cannot both write. It commits when it wrote, which ends the transaction:
    callers run it before the RLS binding.
    """
    written = await db.execute(
        update(model)
        .where(
            model.id == row_id,
            or_(model.last_used_at.is_(None), model.last_used_at < now - LAST_USED_RESOLUTION),
        )
        .values(last_used_at=now)
        .returning(model.id)
        .execution_options(synchronize_session=False)
    )
    if written.first() is not None:
        await db.commit()


async def current_principal(request: Request, db: DbSession, settings: SettingsDep) -> Principal:
    """Authenticate the request: by its bearer if it has one, else its session cookie.

    Another `Authorization` scheme (Basic, Negotiate) is not a credential for this API and is
    ignored, exactly as the middleware ignores it: the cookie decides.

    A bearer that reads (GET or HEAD) also spends a token from its credential's rate limit, once
    the credential is known to be valid, so invalid ones cannot fill the table with buckets. A
    signed-in browser is not limited here, and neither are writes, which are bounded by what the
    credential is allowed to change.
    """
    if is_bearer(request.headers.get("authorization")):
        principal = await authenticate_bearer(request, db)
        if request.method in _READ_METHODS:
            await _limit_bearer_read(request, principal)
        return principal
    return await authenticate_session(request, db, settings)


async def _limit_bearer_read(request: Request, principal: ApiKeyPrincipal | TokenPrincipal) -> None:
    if isinstance(principal, TokenPrincipal):
        key = f"api:token:{principal.token_id}"
    else:
        key = f"api:key:{principal.key_id}"
    await enforce_rate_limit(
        request,
        API_READ_LIMITER,
        key,
        scope="api",
        detail="Too many requests for this credential.",
    )


CurrentPrincipal = Annotated[Principal, Depends(current_principal)]


async def current_user(principal: CurrentPrincipal) -> UserPrincipal:
    """A signed-in browser or a personal access token: someone's account. Never an API key."""
    if isinstance(principal, ApiKeyPrincipal):
        raise key_scope("This route needs a user; API keys cannot use it.")
    return principal


CurrentUser = Annotated[UserPrincipal, Depends(current_user)]


async def current_session(principal: CurrentPrincipal) -> SessionPrincipal:
    """The signed-in session. Tokens and API keys never reach a route that only a session may use.

    A token is refused with `SESSION_REQUIRED` and a key with `KEY_SCOPE`, each telling its
    holder what to use instead.
    """
    if isinstance(principal, TokenPrincipal):
        raise session_required("This route needs a signed-in session; access tokens cannot use it.")
    if not isinstance(principal, SessionPrincipal):
        raise key_scope("This route needs a signed-in session; API keys cannot use it.")
    return principal


CurrentSession = Annotated[SessionPrincipal, Depends(current_session)]


async def refuse_credentials(request: Request, db: DbSession) -> None:
    """For routes that need no sign-in: a bearer is refused, not ignored.

    Without this a request bearing an API key or a token would be served as an anonymous one,
    and since a bearer request skips the Origin check, as one that no CSRF layer looked at. So a
    bearer gets the answer it would get on any route that does not accept it: 401 unless it is a
    valid credential, then 403 `SESSION_REQUIRED` for a token and `KEY_SCOPE` for a key. A request
    with no bearer passes untouched.
    """
    if is_bearer(request.headers.get("authorization")):
        principal = await authenticate_bearer(request, db)
        if isinstance(principal, TokenPrincipal):
            raise session_required("Access tokens cannot use this route.")
        raise key_scope("API keys cannot use this route.")


# `dependencies=ANONYMOUS` on every `/api/v1` route that has no principal dependency. A route
# that forgets it is caught by the route inventory test, not by review.
ANONYMOUS: tuple[DependsMarker, ...] = (Depends(refuse_credentials),)


def require_key_scope(scope: KeyScope) -> Callable[..., Awaitable[ApiKeyPrincipal]]:
    """Authenticate an API key (and only a key: never a cookie) that holds `scope`.

    401 for a missing, unknown, revoked or expired key, then 403 `KEY_SCOPE` if it lacks `scope`.
    """

    async def dependency(request: Request, db: DbSession) -> ApiKeyPrincipal:
        key = await authenticate_api_key(request, db)
        if scope not in key.scopes:
            raise key_scope(f"This API key does not have the `{scope}` scope.")
        return key

    return dependency


@dataclass(frozen=True)
class Access:
    """The outcome of an authorization check for one org- or project-scoped request."""

    auth: UserPrincipal  # a signed-in browser or a personal access token
    org: Organization
    role: MembershipRole
    project: Project | None

    @property
    def user_id(self) -> uuid.UUID:
        return self.auth.user.id

    def can(self, permission: Permission) -> bool:
        """Whether the caller's role holds `permission`, for decisions a handler makes itself.

        A token's scope is not looked at here: `require()` has already refused a read-only token
        before any handler runs.
        """
        return has(self.role, permission)

    def require_project(self) -> Project:
        if self.project is None:
            raise RuntimeError("route is not project-scoped")
        return self.project


@dataclass(frozen=True)
class KeyAccess:
    """An API key reading its own project: no user, no role, and so no `can()`.

    Only routes that opt in with `require(..., allow_api_key=True)` ever receive one, and their
    handlers use nothing but the project.
    """

    key: ApiKeyPrincipal
    project: Project

    def require_project(self) -> Project:
        return self.project


ReadAccess = Access | KeyAccess


def _path_uuid(request: Request, name: str) -> uuid.UUID:
    try:
        return uuid.UUID(request.path_params[name])
    except ValueError as exc:
        raise not_found() from exc


@overload
def require(permission: Permission) -> Callable[..., Awaitable[Access]]: ...
@overload
def require(
    permission: Permission, *, allow_api_key: Literal[False]
) -> Callable[..., Awaitable[Access]]: ...
@overload
def require(
    permission: Permission, *, allow_api_key: Literal[True]
) -> Callable[..., Awaitable[ReadAccess]]: ...
def require(
    permission: Permission, *, allow_api_key: bool = False
) -> Callable[..., Awaitable[ReadAccess]]:
    """Authorize the caller for the org or project named in the route path.

    Non-members get 404, so the existence of other tenants' resources is never
    revealed. Members lacking the permission get 403. For project routes the
    transaction is bound to the project, which row-level security enforces. In an org that
    requires two-factor authentication, members without it get 403 `TWO_FACTOR_REQUIRED`.

    A personal access token is its user: it is checked with the user's memberships, read on every
    request, exactly as a session is. On top of that a `read` token is refused (403 `TOKEN_SCOPE`)
    where the permission is classed `write`, and on any method that is not GET, HEAD or OPTIONS:
    a handler may change something under a read permission (leaving an organization needs only
    `org:read`), and a read-only token must not get to do that. Both come after the role check,
    so a token never learns anything a session of the same user would not, and the answer for a
    role that lacks the permission is the same `FORBIDDEN` whichever the scope.

    An API key is refused (403 `KEY_SCOPE`) unless the route opts in with `allow_api_key=True`,
    which is only for project reads: the key then needs `traces:read` and the project in the
    path must be its own (anything else is 404).
    """
    if allow_api_key and permission is not Permission.PROJECT_READ:
        raise ValueError("API keys may only be allowed on project:read routes")

    async def dependency(
        request: Request, principal: CurrentPrincipal, db: DbSession
    ) -> ReadAccess:
        if isinstance(principal, ApiKeyPrincipal):
            if not allow_api_key:
                raise key_scope("API keys cannot use this route.")
            return await _key_access(request, principal, db)
        return await _member_access(request, principal, db, permission)

    return dependency


async def _key_access(request: Request, key: ApiKeyPrincipal, db: AsyncSession) -> KeyAccess:
    if "project_id" not in request.path_params:
        raise RuntimeError("an API key can only be allowed on a project route")
    # Compared before the scope, and answered like a project that does not exist: a key learns
    # nothing about other projects. The project is bound from the key, never from the path.
    if _path_uuid(request, "project_id") != key.project_id:
        raise not_found()
    if KeyScope.TRACES_READ not in key.scopes:
        raise key_scope(f"This API key does not have the `{KeyScope.TRACES_READ}` scope.")
    project = await db.get(Project, key.project_id)
    if project is None:
        raise not_found()
    await bind_project(db, project.id)
    return KeyAccess(key=key, project=project)


async def _member_access(
    request: Request, auth: UserPrincipal, db: AsyncSession, permission: Permission
) -> Access:
    if "project_id" in request.path_params:
        project_id = _path_uuid(request, "project_id")
        row = (
            await db.execute(
                select(Project, Organization, Membership.role)
                .join(Organization, Organization.id == Project.org_id)
                .join(
                    Membership,
                    (Membership.org_id == Project.org_id) & (Membership.user_id == auth.user.id),
                )
                .where(Project.id == project_id)
            )
        ).one_or_none()
        if row is None:
            raise not_found()
        project, org, role = row
    elif "org_id" in request.path_params:
        org_id = _path_uuid(request, "org_id")
        org_row = (
            await db.execute(
                select(Organization, Membership.role)
                .join(Membership, Membership.org_id == Organization.id)
                .where(Organization.id == org_id, Membership.user_id == auth.user.id)
            )
        ).one_or_none()
        if org_row is None:
            raise not_found()
        org, role = org_row
        project = None
    else:
        raise RuntimeError("require() needs an org_id or project_id path parameter")

    if org.require_2fa and not auth.user.totp_enabled:
        # After membership (a stranger still gets 404) and before the permission check: the
        # fix is the same whatever the member tried, and `/auth/*` stays open to apply it.
        raise ProblemError(
            403,
            "TWO_FACTOR_REQUIRED",
            "This organization requires two-factor authentication. Turn it on in your "
            "account security settings.",
        )
    if not has(role, permission):
        raise forbidden()
    if (
        isinstance(auth, TokenPrincipal)
        and auth.scope is TokenScope.READ
        and (permission_class(permission) == "write" or request.method not in _SAFE_METHODS)
    ):
        raise token_scope(
            "This access token is read-only. Create a token with the `write` scope to do this."
        )
    if project is not None:
        await bind_project(db, project.id)
    return Access(auth=auth, org=org, role=role, project=project)
