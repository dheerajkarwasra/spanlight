"""Sign in with GitHub and Google: start, callback, and the signed-in user's linked providers.

`start` and `callback` are browser navigations, so a failed callback is not a problem+json body
but a redirect that carries an error code in the query string. The other routes are ordinary
JSON. The rules for what a profile may do are in `app.auth.oauth_service`.
"""

from typing import Annotated
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
import structlog
from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    ANONYMOUS,
    SESSION_COOKIE,
    Clock,
    ClockDep,
    CurrentSession,
    DbSession,
    SettingsDep,
    client_ip,
    utcnow,
)
from app.api.schemas import OAuthIdentityOut, OAuthProviderOut
from app.api.v1.auth import set_auth_cookies
from app.auth.login_challenge import issue_challenge
from app.auth.oauth_client import ProviderError, authorization_url, fetch_profile
from app.auth.oauth_providers import PROVIDERS, OAuthProfile
from app.auth.oauth_service import (
    IdentityNotLinkedError,
    LastSignInMethodError,
    OAuthCode,
    OAuthRefused,
    link_identity,
    sign_in_with_profile,
    unlink_identity,
)
from app.auth.oauth_state import (
    STATE_COOKIE,
    Intent,
    OAuthState,
    clear_state_cookie,
    new_state,
    read_state,
    safe_next,
    set_state_cookie,
    state_matches,
)
from app.config import Settings
from app.core.errors import conflict, forbidden, not_found, unauthorized
from app.db.models import AuditAction, OAuthIdentity, User
from app.services.audit import record_user_audit_in_each_org
from app.services.credentials import lock_user_for_sign_in
from app.services.demo import is_demo_user
from app.services.sessions import ActiveSession, create_session, resolve_session

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/auth/oauth", tags=["auth"])

DEFAULT_SIGN_IN_NEXT = "/"
DEFAULT_LINK_NEXT = "/settings/security"
LOGIN_PATH = "/login"
TWO_FACTOR_PATH = "/login/two-factor"


def get_oauth_transport(request: Request) -> httpx.AsyncBaseTransport | None:
    """The transport for provider calls: `None` (the network) unless the app was built with one."""
    transport: httpx.AsyncBaseTransport | None = request.app.state.oauth_transport
    return transport


OAuthTransport = Annotated[httpx.AsyncBaseTransport | None, Depends(get_oauth_transport)]


def configured_provider(provider: str, settings: SettingsDep) -> str:
    """A provider the operator has set up; anything else is 404 and the route does nothing."""
    if provider not in settings.oauth_providers:
        raise not_found()
    return provider


ConfiguredProvider = Annotated[str, Depends(configured_provider)]


# --- JSON routes -------------------------------------------------------------------------------


@router.get("/providers", response_model=list[OAuthProviderOut], dependencies=ANONYMOUS)
async def list_providers(settings: SettingsDep) -> list[OAuthProviderOut]:
    """The providers that can be used to sign in, so the login page shows only working buttons."""
    return [OAuthProviderOut(provider=name) for name in settings.oauth_providers]


@router.get("/identities", response_model=list[OAuthIdentityOut])
async def list_identities(auth: CurrentSession, db: DbSession) -> list[OAuthIdentity]:
    return list(
        (
            await db.scalars(
                select(OAuthIdentity)
                .where(OAuthIdentity.user_id == auth.user.id)
                .order_by(OAuthIdentity.created_at, OAuthIdentity.provider)
            )
        ).all()
    )


@router.delete("/{provider}", status_code=status.HTTP_204_NO_CONTENT)
async def unlink(provider: str, auth: CurrentSession, db: DbSession, request: Request) -> None:
    """Unlink a provider account. The provider need not still be configured."""
    if provider not in PROVIDERS:
        raise not_found()
    try:
        await unlink_identity(db, auth.user, provider, ip=client_ip(request))
    except IdentityNotLinkedError:
        raise not_found() from None
    except LastSignInMethodError:
        raise conflict(
            "LAST_SIGN_IN_METHOD",
            "This is the only way to sign in to this account. Set a password or link another "
            "provider first.",
        ) from None
    await db.commit()


# --- the browser flow --------------------------------------------------------------------------


@router.get(
    "/{provider}/start",
    dependencies=ANONYMOUS,
    response_class=RedirectResponse,
    status_code=status.HTTP_302_FOUND,
    responses={302: {"description": "Redirect to the provider's sign-in page; sets `spl_oauth`."}},
)
async def start(
    provider: ConfiguredProvider,
    request: Request,
    db: DbSession,
    settings: SettingsDep,
    intent: Intent = "sign_in",
    next_path: Annotated[str | None, Query(alias="next")] = None,
) -> RedirectResponse:
    """Send the browser to the provider. `intent=link` links it to the signed-in user instead."""
    now = utcnow()
    user_id = None
    if intent == "link":
        active = await _optional_session(request, db)
        if active is None:
            raise unauthorized()
        # Refused here so the person is told at once; `link_identity` checks both again at the
        # callback, which a state signed before the rules changed could still reach.
        if is_demo_user(active.user):
            raise forbidden("The demo account cannot link a sign-in provider.")
        if settings.is_email_configured and not active.user.email_verified:
            raise conflict(
                "EMAIL_UNVERIFIED", "Verify your email address before linking a sign-in provider."
            )
        user_id = active.user.id
    default = DEFAULT_LINK_NEXT if intent == "link" else DEFAULT_SIGN_IN_NEXT
    state = new_state(
        provider=provider,
        intent=intent,
        next=safe_next(next_path) if next_path else default,
        user_id=user_id,
        now=now,
    )
    response = RedirectResponse(
        authorization_url(settings, provider, state=state.state, verifier=state.verifier),
        status_code=status.HTTP_302_FOUND,
    )
    set_state_cookie(response, settings, state)
    return _uncacheable(response)


@router.get(
    "/{provider}/callback",
    dependencies=ANONYMOUS,
    response_class=RedirectResponse,
    status_code=status.HTTP_302_FOUND,
    responses={
        302: {
            "description": "Redirect to `next` (with session cookies for a sign-in), or to "
            "`/login?error=<CODE>` (to `next` with `error=<CODE>` for a link). Clears `spl_oauth`."
        }
    },
)
async def callback(
    provider: ConfiguredProvider,
    request: Request,
    db: DbSession,
    settings: SettingsDep,
    transport: OAuthTransport,
    clock: ClockDep,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    """Finish a sign-in or link, then redirect: to `next` on success, to an error page if not.

    Every query parameter is optional so a callback that is wrong in any way still gets the
    redirect the user can read, not a JSON validation error.
    """
    stored = read_state(
        settings.secret_key.get_secret_value(), request.cookies.get(STATE_COOKIE), now=utcnow()
    )
    if stored is None or not state_matches(stored, provider, state):
        return _finish(settings, _failure(settings, stored, OAuthCode.STATE, provider))
    # The state is ours and current from here on, so `stored.next` and `stored.intent` can be
    # trusted for where to send the browser.
    if error is not None or not code:
        # The user said no at the provider, or the provider sent nothing usable.
        return _finish(settings, _failure(settings, stored, OAuthCode.PROVIDER_ERROR, provider))

    try:
        profile = await fetch_profile(
            settings, provider, code=code, verifier=stored.verifier, transport=transport
        )
    except ProviderError as failure:
        logger.warning("oauth_provider_error", provider=provider, reason=str(failure))
        return _finish(settings, _failure(settings, stored, OAuthCode.PROVIDER_ERROR, provider))

    try:
        if stored.intent == "link":
            response = await _link(request, db, settings, stored, profile)
        else:
            user = await sign_in_with_profile(db, profile, ip=client_ip(request))
            response = await finish_sign_in(
                request, db, settings, user, next_path=stored.next, clock=clock
            )
    except OAuthRefused as refused:
        await db.rollback()
        return _finish(settings, _failure(settings, stored, refused.code, provider))
    logger.info("oauth_completed", provider=provider, intent=stored.intent)
    return _finish(settings, response)


async def finish_sign_in(
    request: Request,
    db: AsyncSession,
    settings: Settings,
    user: User,
    *,
    next_path: str,
    clock: Clock,
) -> RedirectResponse:
    """Sign the user in: create the session, set the cookies, redirect to `next_path`.

    The one place a successful OAuth callback turns into a session. A user with two-factor
    authentication gets no session here: the provider vouched for who they are, not for the
    second factor, so the browser goes to the second step with a challenge instead.

    What `sign_in_with_profile` wrote (a new user, or an identity linked because the verified
    emails matched) is committed in both cases. That identity is the user's own verified provider
    account, and every sign-in through it still needs the second factor, so keeping it grants
    nothing.

    Whether the second factor is needed is read with the user row locked, which it stays until
    the commit below. `user` was read earlier in the request, and two-factor authentication may
    have been turned on since; deciding from it would hand that account a session without the
    second step. Raises `OAuthRefused` if the user is gone.
    """
    locked = await lock_user_for_sign_in(db, user.id)
    if locked is None:
        raise OAuthRefused(OAuthCode.STATE)  # nobody to sign in: start again
    if locked.totp_enabled:
        await db.commit()
        challenge = issue_challenge(settings, user.id, clock(), password_hash=locked.password_hash)
        # In the fragment, never the query: a fragment is not sent to any server or logged by it.
        return RedirectResponse(
            f"{settings.app_base_url}{TWO_FACTOR_PATH}#challenge={challenge.token}",
            status_code=status.HTTP_302_FOUND,
        )

    now = utcnow()
    ip = client_ip(request)
    issued = await create_session(
        db, user.id, now=now, ip=ip, user_agent=request.headers.get("user-agent")
    )
    await record_user_audit_in_each_org(
        db, user_id=user.id, action=AuditAction.USER_LOGIN, ip=ip, metadata={"method": "oauth"}
    )
    await db.commit()
    response = RedirectResponse(
        f"{settings.app_base_url}{next_path}", status_code=status.HTTP_302_FOUND
    )
    set_auth_cookies(
        response, settings, issued.token, max_age=issued.session.absolute_expires_at - now
    )
    return response


async def _link(
    request: Request,
    db: AsyncSession,
    settings: Settings,
    stored: OAuthState,
    profile: OAuthProfile,
) -> RedirectResponse:
    active = await _optional_session(request, db)
    # The account that finishes the link must be the one that started it. Otherwise a state
    # started in one session and finished in another would attach the provider account to
    # whoever happens to be signed in at the callback.
    if active is None or active.user.id != stored.user_id:
        raise OAuthRefused(OAuthCode.STATE)
    await link_identity(
        db,
        active.user,
        profile,
        require_verified_email=settings.is_email_configured,
        ip=client_ip(request),
    )
    await db.commit()
    return RedirectResponse(
        f"{settings.app_base_url}{stored.next}", status_code=status.HTTP_302_FOUND
    )


async def _optional_session(request: Request, db: AsyncSession) -> ActiveSession | None:
    """The signed-in session if there is a live one. These GET routes need no CSRF token."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    return await resolve_session(db, token, now=utcnow())


def _failure(
    settings: Settings, stored: OAuthState | None, code: OAuthCode, provider: str
) -> RedirectResponse:
    """The redirect for a failed callback.

    A sign-in goes to the login page. A link starts from a signed-in page, and the login page
    would bounce a signed-in user away and lose the error, so it goes back to where the link
    started with `error` appended.
    """
    logger.warning("oauth_refused", provider=provider, code=code.value)
    if stored is not None and stored.intent == "link":
        path = _with_error(stored.next, code)
    else:
        path = f"{LOGIN_PATH}?{urlencode({'error': code.value})}"
    return RedirectResponse(f"{settings.app_base_url}{path}", status_code=status.HTTP_302_FOUND)


def _with_error(path: str, code: OAuthCode) -> str:
    parts = urlsplit(path)
    query = urlencode([*parse_qsl(parts.query, keep_blank_values=True), ("error", code.value)])
    return urlunsplit(("", "", parts.path, query, parts.fragment))


def _finish(settings: Settings, response: RedirectResponse) -> RedirectResponse:
    """Whatever the callback decided, it spent the state: drop the cookie, and keep it uncached."""
    clear_state_cookie(response, settings)
    return _uncacheable(response)


def _uncacheable(response: RedirectResponse) -> RedirectResponse:
    # These redirects carry one-time codes and set session cookies; no cache may keep them.
    response.headers["Cache-Control"] = "no-store"
    return response
