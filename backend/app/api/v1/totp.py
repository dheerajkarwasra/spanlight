"""Two-factor authentication: set it up, turn it on, sign in with it, turn it off.

`status`, `setup`, `enable` and `disable` are for a signed-in user. `verify` is the second step of
signing in, so it has no session: what it presents instead is the challenge that `login` (or the
OAuth callback) handed out after the first step passed.
"""

import math
from datetime import timedelta

import structlog
from fastapi import APIRouter, Request, Response, status

from app.api.deps import (
    ANONYMOUS,
    ClockDep,
    CurrentSession,
    DbSession,
    SettingsDep,
    client_ip,
    utcnow,
)
from app.api.schemas import (
    LoginSignedInOut,
    TotpCodeIn,
    TotpEnabledOut,
    TotpSetupOut,
    TotpStatusOut,
    TotpVerifyIn,
    UserOut,
)
from app.api.v1.auth import set_auth_cookies
from app.auth import totp_service
from app.auth.login_challenge import read_challenge
from app.auth.login_throttle import enforce_login_throttle
from app.config import Settings
from app.core.crypto import CryptoNotConfigured
from app.core.errors import (
    ProblemError,
    conflict,
    forbidden,
    not_configured,
    too_many_requests,
)
from app.core.security import mark_uncacheable
from app.core.throttle import check_and_record
from app.db.models import AuditAction, LoginAttempt, User
from app.services.audit import record_user_audit_in_each_org
from app.services.demo import is_demo_user
from app.services.sessions import create_session

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/auth/totp", tags=["auth"])

NEEDS_KEYS = (
    "Two-factor authentication needs CREDENTIALS_KEYS to be set; generate a key with "
    "`openssl rand -base64 32` and set it as `<key id>:<key>`."
)
# Turning two-factor authentication off needs a code, so a stolen session must not be able to try
# them all. The limit is per user and counts every attempt.
DISABLE_SCOPE = "totp_disable"
MAX_DISABLE_ATTEMPTS = 5
DISABLE_WINDOW = timedelta(minutes=15)


def _require_crypto(settings: Settings) -> None:
    if not settings.is_crypto_configured:
        raise not_configured(NEEDS_KEYS)


def _refuse_demo(user: User) -> None:
    if is_demo_user(user):
        # One shared, anonymous account: a visitor who turned it on would lock out every later one.
        raise forbidden("The demo account cannot use two-factor authentication.")


def _invalid_code() -> ProblemError:
    return ProblemError(
        422, "INVALID_TOTP_CODE", "That code is not valid. Check the code and try again."
    )


def _no_store(response: Response) -> None:
    # Secrets and recovery codes are for the person who asked and for no cache.
    mark_uncacheable(response)


@router.get("", response_model=TotpStatusOut)
async def totp_status(auth: CurrentSession, db: DbSession) -> TotpStatusOut:
    user = auth.user
    return TotpStatusOut(
        enabled=user.totp_enabled,
        enabled_at=user.totp_enabled_at,
        recovery_codes_remaining=(
            await totp_service.recovery_codes_remaining(db, user.id) if user.totp_enabled else 0
        ),
    )


@router.post("/setup", response_model=TotpSetupOut)
async def totp_setup(
    response: Response, auth: CurrentSession, db: DbSession, settings: SettingsDep
) -> TotpSetupOut:
    """Make a secret for an authenticator app. Replaces one from an earlier, unconfirmed setup."""
    # Not configured first: it is the operator's state, not something about the user.
    _require_crypto(settings)
    _refuse_demo(auth.user)
    try:
        info = await totp_service.begin_setup(db, settings, auth.user)
    except totp_service.TotpAlreadyEnabledError:
        raise conflict(
            "TOTP_ALREADY_ENABLED", "Two-factor authentication is already turned on."
        ) from None
    await db.commit()
    _no_store(response)
    return TotpSetupOut(secret=info.secret, otpauth_url=info.otpauth_url)


@router.post("/enable", response_model=TotpEnabledOut)
async def totp_enable(
    body: TotpCodeIn,
    request: Request,
    response: Response,
    auth: CurrentSession,
    db: DbSession,
    settings: SettingsDep,
    clock: ClockDep,
) -> TotpEnabledOut:
    """Turn it on with a code from the app. Returns the recovery codes, this once."""
    _refuse_demo(auth.user)
    if settings.is_email_configured and not auth.user.email_verified:
        # Anyone can register an address that is not theirs. If that account could enrol a
        # second factor, the address's real owner could recover it by email and still be shut
        # out of it by a factor they never set.
        raise conflict(
            "EMAIL_UNVERIFIED", "Verify your email address before turning on two-factor sign-in."
        )
    try:
        codes = await totp_service.enable(
            db, settings, auth.user, body.code, now=clock(), ip=client_ip(request)
        )
    except totp_service.TotpAlreadyEnabledError:
        raise conflict(
            "TOTP_ALREADY_ENABLED", "Two-factor authentication is already turned on."
        ) from None
    except totp_service.NoPendingSetupError:
        raise ProblemError(
            422,
            "INVALID_TOTP_CODE",
            "Start the setup first, then enter a code from your authenticator app.",
        ) from None
    except totp_service.InvalidTotpCodeError:
        raise _invalid_code() from None
    except CryptoNotConfigured:
        raise not_configured(NEEDS_KEYS) from None
    await db.commit()
    # Ids and outcomes only: never a secret, a code or a recovery code.
    logger.info("totp_enabled", user_id=str(auth.user.id))
    _no_store(response)
    return TotpEnabledOut(recovery_codes=codes)


@router.post("/disable", status_code=status.HTTP_204_NO_CONTENT)
async def totp_disable(
    body: TotpCodeIn,
    request: Request,
    auth: CurrentSession,
    db: DbSession,
    settings: SettingsDep,
    clock: ClockDep,
) -> None:
    """Turn it off. Needs a valid code (TOTP or recovery), which is spent."""
    retry_after = await check_and_record(
        db, DISABLE_SCOPE, str(auth.user.id), MAX_DISABLE_ATTEMPTS, DISABLE_WINDOW
    )
    if retry_after is not None:
        raise too_many_requests(
            math.ceil(retry_after), "Too many attempts to turn off two-factor authentication."
        )
    try:
        await totp_service.disable(
            db, settings, auth.user, body.code, now=clock(), ip=client_ip(request)
        )
    except totp_service.TotpNotEnabledError:
        raise conflict("TOTP_NOT_ENABLED", "Two-factor authentication is not turned on.") from None
    except totp_service.InvalidTotpCodeError:
        # The attempt must stay counted even though the request fails.
        await db.commit()
        raise _invalid_code() from None
    except CryptoNotConfigured:
        raise not_configured(NEEDS_KEYS) from None
    await db.commit()
    logger.info("totp_disabled", user_id=str(auth.user.id))


@router.post("/verify", response_model=LoginSignedInOut, dependencies=ANONYMOUS)
async def totp_verify(
    body: TotpVerifyIn,
    request: Request,
    response: Response,
    db: DbSession,
    settings: SettingsDep,
    clock: ClockDep,
) -> LoginSignedInOut:
    """The second step of signing in: a challenge and a code make a session.

    The user row is locked before the failure count is read, so concurrent attempts for one
    account run one after another: the limit is exact, and a code cannot be spent twice. Wrong
    codes are recorded as failed sign-ins, so they share the password's limit. A challenge issued
    before the password changed is refused: it passed the first step against a password that no
    longer exists.
    """
    claims = read_challenge(settings, body.challenge, clock())
    user = await totp_service.lock_user(db, claims.user_id) if claims is not None else None
    # Read under the lock, so a password change that commits first is seen: the challenge then
    # names a password that no longer exists.
    if (
        claims is None
        or user is None
        or not user.totp_enabled
        or not claims.matches(user.password_hash)
    ):
        # Forged, expired, for nobody, for an account that has no second factor (any more), or
        # issued before the password changed. All the same answer, and none of them gets as far
        # as a code.
        logger.info("totp_challenge_rejected")
        raise ProblemError(
            401,
            "TOTP_CHALLENGE_INVALID",
            "This sign-in has expired or is not valid. Enter your password again.",
        )

    now = utcnow()
    ip = client_ip(request)
    await enforce_login_throttle(db, email=user.email, ip=ip, now=now)
    try:
        accepted = await totp_service.verify(db, settings, user, body.code, now=clock())
    except CryptoNotConfigured:
        raise not_configured(NEEDS_KEYS) from None
    db.add(LoginAttempt(email=user.email, ip=ip, succeeded=accepted, created_at=now))
    await record_user_audit_in_each_org(
        db,
        user_id=user.id,
        action=AuditAction.USER_LOGIN,
        ip=ip,
        metadata={"method": "totp"},
        outcome="success" if accepted else "failure",
        reason=None if accepted else "invalid_totp",
    )
    if not accepted:
        await db.commit()
        logger.warning("totp_code_rejected", user_id=str(user.id))
        raise ProblemError(
            401, "INVALID_TOTP_CODE", "That code is not valid. Check the code and try again."
        )

    issued = await create_session(
        db, user.id, now=now, ip=ip, user_agent=request.headers.get("user-agent")
    )
    await db.commit()
    set_auth_cookies(
        response, settings, issued.token, max_age=issued.session.absolute_expires_at - now
    )
    return LoginSignedInOut(status="signed_in", user=UserOut.model_validate(user))
