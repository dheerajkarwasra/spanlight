"""Authentication: signup, login (throttled), logout, current user, sessions, email verification,
password reset."""

import math
import uuid
from datetime import timedelta

import structlog
from fastapi import APIRouter, Request, Response, status
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app.api.deps import (
    ANONYMOUS,
    CSRF_COOKIE,
    SESSION_COOKIE,
    ClockDep,
    CurrentSession,
    CurrentUser,
    DbSession,
    SettingsDep,
    client_ip,
    utcnow,
)
from app.api.principals import SessionPrincipal, TokenPrincipal
from app.api.schemas import (
    AcceptedOut,
    EmailVerifyConfirmIn,
    LoginIn,
    LoginOut,
    LoginSignedInOut,
    LoginTotpRequiredOut,
    MembershipOut,
    MeOut,
    OrgOut,
    PasswordForgotIn,
    PasswordResetIn,
    SessionOut,
    SignupIn,
    UserOut,
)
from app.api.schemas.auth import ActivationChecklistIn, CapabilitiesOut
from app.auth.email_tokens import consume_token
from app.auth.login_challenge import issue_challenge
from app.auth.login_throttle import enforce_login_throttle
from app.auth.password_reset import (
    check_forgot_limits,
    complete_password_reset,
    enqueue_reset_email,
)
from app.auth.verification import enqueue_verification_email
from app.config import Settings
from app.core.errors import (
    ProblemError,
    conflict,
    forbidden,
    not_configured,
    not_found,
    token_scope,
    too_many_requests,
)
from app.core.security import (
    hash_password_async,
    new_csrf_token,
    verify_csrf_token,
    verify_password_async,
)
from app.core.throttle import check_and_record
from app.db.models import (
    AuditAction,
    EmailTokenKind,
    LoginAttempt,
    Membership,
    Organization,
    Session,
    TokenScope,
    User,
)
from app.services.audit import record_user_audit_in_each_org
from app.services.credentials import LockedUser, lock_user_for_sign_in
from app.services.demo import is_demo_user
from app.services.sessions import create_session, revoke_other_sessions, revoke_session

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

INVALID_CREDENTIALS = "Invalid email or password."

SIGNUP_IP_SCOPE = "signup_ip"
MAX_SIGNUPS_PER_IP = 10
SIGNUP_WINDOW = timedelta(hours=1)

VERIFY_REQUEST_SCOPE = "email_verify"
MAX_VERIFY_REQUESTS_PER_HOUR = 3
VERIFY_REQUEST_WINDOW = timedelta(hours=1)


def _require_email_configured(settings: Settings) -> None:
    if not settings.is_email_configured:
        raise not_configured(
            "Email is not configured. Set EMAIL_PROVIDER to resend or smtp, or set "
            "EMAIL_CONSOLE_FILE to write emails to a file."
        )


def set_auth_cookies(
    response: Response, settings: Settings, session_token: str, *, max_age: timedelta
) -> None:
    max_age_seconds = int(max_age.total_seconds())
    response.set_cookie(
        SESSION_COOKIE,
        session_token,
        max_age=max_age_seconds,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="lax",
        path="/",
    )
    set_csrf_cookie(response, settings, session_token, max_age_seconds=max_age_seconds)


def set_csrf_cookie(
    response: Response, settings: Settings, session_token: str, *, max_age_seconds: int
) -> None:
    # Readable by frontend JavaScript, which echoes it in the X-CSRF-Token header.
    response.set_cookie(
        CSRF_COOKIE,
        new_csrf_token(settings.secret_key.get_secret_value(), session_token),
        max_age=max_age_seconds,
        httponly=False,
        secure=settings.secure_cookies,
        samesite="lax",
        path="/",
    )


def clear_auth_cookies(response: Response, settings: Settings) -> None:
    for name in (SESSION_COOKIE, CSRF_COOKIE):
        response.delete_cookie(name, path="/", secure=settings.secure_cookies, samesite="lax")


@router.post(
    "/signup",
    status_code=status.HTTP_201_CREATED,
    response_model=UserOut,
    dependencies=ANONYMOUS,
)
async def signup(
    body: SignupIn, request: Request, response: Response, db: DbSession, settings: SettingsDep
) -> User:
    now = utcnow()
    ip = client_ip(request)
    if ip is not None:
        retry_after = await check_and_record(
            db, SIGNUP_IP_SCOPE, ip, MAX_SIGNUPS_PER_IP, SIGNUP_WINDOW
        )
        if retry_after is not None:
            raise too_many_requests(
                math.ceil(retry_after), "Too many sign-up attempts. Try again later."
            )
        # Commit the attempt now. Every request pays for an argon2id hash and may still fail
        # (for example `EMAIL_TAKEN`), and a rolled-back attempt would not count, so probing
        # existing addresses would be an unthrottled stream of hashes.
        await db.commit()
    user = User(
        email=body.email, password_hash=await hash_password_async(body.password), name=body.name
    )
    db.add(user)
    try:
        await db.flush()
    except IntegrityError as exc:
        raise ProblemError(
            409, "EMAIL_TAKEN", "An account with this email already exists."
        ) from exc
    if settings.is_email_configured:
        # In the signup transaction: the account, its token and the queued email commit together.
        await enqueue_verification_email(db, settings, user)

    issued = await create_session(
        db,
        user.id,
        now=now,
        ip=ip,
        user_agent=request.headers.get("user-agent"),
    )
    await db.commit()
    set_auth_cookies(
        response, settings, issued.token, max_age=issued.session.absolute_expires_at - now
    )
    return user


@router.post("/login", response_model=LoginOut, dependencies=ANONYMOUS)
async def login(
    body: LoginIn,
    request: Request,
    response: Response,
    db: DbSession,
    settings: SettingsDep,
    clock: ClockDep,
) -> LoginSignedInOut | LoginTotpRequiredOut:
    """Check the password. A user with two-factor authentication gets a challenge, not a session.

    The challenge is issued only after everything a session needs has held (throttle, password,
    a password that is still current), so it means exactly "the first step passed".
    """
    now = utcnow()
    ip = client_ip(request)
    await enforce_login_throttle(db, email=body.email, ip=ip, now=now)

    user = await db.scalar(select(User).where(User.email == body.email))
    # A user who signed up with GitHub or Google has no hash: like an unknown address, they get
    # the dummy verification, so this answer takes as long as any other and says nothing about
    # which addresses exist.
    verified_hash = user.password_hash if user else None
    # End the read transaction before hashing, which returns the connection to the pool. Hashing
    # waits for a slot in a small executor; holding a connection idle in transaction meanwhile
    # would let a burst of sign-ins exhaust the pool that ingestion and the dashboard share. Nothing
    # depends on this transaction: the locked re-read below checks the user again.
    await db.commit()
    password_ok = await verify_password_async(verified_hash, body.password)
    locked: LockedUser | None = None
    if user is not None and password_ok:
        # Hashing is slow enough for a password reset, or turning on two-factor authentication,
        # to commit while it ran. Lock the user and check the password is still the one that
        # verified; a reset that comes later waits for this transaction and then ends the
        # session created below. Whether the second step is needed is decided from this locked
        # read too, never from `user`, which was read before the hashing.
        locked = await lock_user_for_sign_in(db, user.id)
        password_ok = locked is not None and locked.has_password_hash(verified_hash)
    db.add(LoginAttempt(email=body.email, ip=ip, succeeded=password_ok, created_at=now))
    if user is None or locked is None or not password_ok:
        if user is not None:
            # An unknown address belongs to no org: it stays in `login_attempts` only.
            await record_user_audit_in_each_org(
                db,
                user_id=user.id,
                action=AuditAction.USER_LOGIN,
                ip=ip,
                metadata={"method": "password"},
                outcome="failure",
                reason="invalid_password",
            )
        await db.commit()
        raise ProblemError(401, "INVALID_CREDENTIALS", INVALID_CREDENTIALS)

    if locked.totp_enabled:
        # Commit now: it keeps the attempt and releases the lock taken above. No session, no
        # cookie: the second step creates them.
        await db.commit()
        challenge = issue_challenge(settings, user.id, clock(), password_hash=locked.password_hash)
        return LoginTotpRequiredOut(
            status="totp_required", challenge=challenge.token, expires_at=challenge.expires_at
        )

    issued = await create_session(
        db, user.id, now=now, ip=ip, user_agent=request.headers.get("user-agent")
    )
    await record_user_audit_in_each_org(
        db, user_id=user.id, action=AuditAction.USER_LOGIN, ip=ip, metadata={"method": "password"}
    )
    await db.commit()
    set_auth_cookies(
        response, settings, issued.token, max_age=issued.session.absolute_expires_at - now
    )
    return LoginSignedInOut(status="signed_in", user=UserOut.model_validate(user))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(auth: CurrentSession, db: DbSession, settings: SettingsDep) -> Response:
    await revoke_session(db, auth.session.id)
    await record_user_audit_in_each_org(db, user_id=auth.user.id, action=AuditAction.USER_LOGOUT)
    await db.commit()
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_auth_cookies(response, settings)
    return response


@router.get("/me", response_model=MeOut)
async def me(
    request: Request, response: Response, auth: CurrentUser, db: DbSession, settings: SettingsDep
) -> MeOut:
    """The caller's account and memberships: for a browser session, and for a personal access token.

    This is the one `/auth` route a token may call, so a tool can check that its token works and
    whose it is.
    """
    rows = (
        await db.execute(
            select(Organization, Membership.role)
            .join(Membership, Membership.org_id == Organization.id)
            .where(Membership.user_id == auth.user.id)
            .order_by(Organization.created_at)
        )
    ).all()

    if isinstance(auth, SessionPrincipal):
        _reissue_csrf_cookie(request, response, settings, auth)

    return MeOut(
        user=UserOut.model_validate(auth.user),
        memberships=[
            MembershipOut(org=OrgOut.model_validate(org), role=role) for org, role in rows
        ],
        has_password=auth.user.has_password,
        totp_enabled=auth.user.totp_enabled,
        email_verification_required=(
            settings.is_email_configured
            and not auth.user.email_verified
            and not is_demo_user(auth.user)
        ),
        activation_checklist_dismissed=auth.user.activation_checklist_dismissed_at is not None,
        capabilities=CapabilitiesOut(
            gateway_mode=settings.gateway_mode,
            credentials_configured=settings.is_crypto_configured,
            exports_configured=settings.is_object_storage_configured,
        ),
    )


@router.put("/me/activation-checklist", status_code=status.HTTP_204_NO_CONTENT)
async def set_activation_checklist(
    body: ActivationChecklistIn, auth: CurrentUser, db: DbSession
) -> None:
    """Hide or show the setup checklist for the caller, in every project. Idempotent: hiding it
    again keeps the time it was first hidden. A session or a `write` token; the row is always the
    caller's own."""
    if isinstance(auth, TokenPrincipal) and auth.scope is TokenScope.READ:
        raise token_scope(
            "This access token is read-only. Create a token with the `write` scope to do this."
        )
    if is_demo_user(auth.user):
        # Every visitor is this one user: one visitor hiding it would hide it for all of them.
        raise forbidden("The demo account can't hide the setup checklist.")
    dismissed_at = (
        func.coalesce(User.activation_checklist_dismissed_at, func.now())
        if body.dismissed
        else None
    )
    await db.execute(
        update(User)
        .where(User.id == auth.user.id)
        .values(activation_checklist_dismissed_at=dismissed_at)
    )
    await db.commit()


def _reissue_csrf_cookie(
    request: Request, response: Response, settings: Settings, auth: SessionPrincipal
) -> None:
    """Re-issue the CSRF cookie if the browser lost it, so the SPA can recover.

    The session cookie is what authenticated this request (a session principal never comes from
    anything else), and the CSRF token is bound to its value.
    """
    session_token = request.cookies.get(SESSION_COOKIE, "")
    csrf_cookie = request.cookies.get(CSRF_COOKIE)
    secret = settings.secret_key.get_secret_value()
    if not csrf_cookie or not verify_csrf_token(secret, session_token, csrf_cookie):
        remaining = auth.session.absolute_expires_at - utcnow()
        set_csrf_cookie(
            response, settings, session_token, max_age_seconds=int(remaining.total_seconds())
        )


def _refuse_demo_sessions(user: User) -> None:
    if is_demo_user(user):
        # Every visitor is this one user, so these sessions are other people's: ending them would
        # sign visitors out of each other's demo.
        raise forbidden("The demo account's sessions are shared and cannot be ended.")


@router.get("/sessions", response_model=list[SessionOut])
async def list_sessions(auth: CurrentSession, db: DbSession) -> list[SessionOut]:
    query = select(Session).where(
        Session.user_id == auth.user.id, Session.absolute_expires_at > utcnow()
    )
    if is_demo_user(auth.user):
        # The other sessions of the shared account belong to other visitors, and their address and
        # browser are not for this one to see.
        query = query.where(Session.id == auth.session.id)
    sessions = (await db.scalars(query.order_by(Session.last_seen_at.desc()))).all()
    return [
        SessionOut(
            id=session.id,
            created_at=session.created_at,
            last_seen_at=session.last_seen_at,
            ip=str(session.ip) if session.ip else None,
            user_agent=session.user_agent,
            current=session.id == auth.session.id,
        )
        for session in sessions
    ]


@router.delete("/sessions", status_code=status.HTTP_204_NO_CONTENT)
async def delete_other_sessions(auth: CurrentSession, db: DbSession) -> None:
    """Sign out everywhere else: end every session of the user except the one making the call."""
    _refuse_demo_sessions(auth.user)
    ended = await revoke_other_sessions(db, auth.user.id, keep=auth.session.id)
    await record_user_audit_in_each_org(
        db,
        user_id=auth.user.id,
        action=AuditAction.USER_SESSION_REVOKE,
        metadata={"count": ended, "kept_current": True},
    )
    await db.commit()
    logger.info("sessions_revoked_others", user_id=str(auth.user.id), count=ended)


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(session_id: uuid.UUID, auth: CurrentSession, db: DbSession) -> None:
    _refuse_demo_sessions(auth.user)
    session = await db.get(Session, session_id)
    if session is None or session.user_id != auth.user.id:
        raise not_found()
    await revoke_session(db, session.id)
    await record_user_audit_in_each_org(
        db,
        user_id=auth.user.id,
        action=AuditAction.USER_SESSION_REVOKE,
        target_type="session",
        target_id=session.id,
    )
    await db.commit()


@router.post(
    "/email/verify/request", status_code=status.HTTP_202_ACCEPTED, response_model=AcceptedOut
)
async def request_email_verification(
    auth: CurrentSession, db: DbSession, settings: SettingsDep
) -> AcceptedOut:
    """Send the signed-in user a fresh verification link, at most 3 per hour."""
    # Not configured is checked first: it is the operator's state, not something about the user.
    _require_email_configured(settings)
    user = auth.user
    if is_demo_user(user):
        # Shared and anonymous: whoever is "signed in" as it must not make the server mail it.
        raise forbidden("The demo account cannot receive email.")
    if user.email_verified:
        raise conflict("EMAIL_ALREADY_VERIFIED", "This email address is already verified.")

    retry_after = await check_and_record(
        db,
        VERIFY_REQUEST_SCOPE,
        str(user.id),
        MAX_VERIFY_REQUESTS_PER_HOUR,
        VERIFY_REQUEST_WINDOW,
    )
    if retry_after is not None:
        raise too_many_requests(
            math.ceil(retry_after), "Too many verification emails requested. Try again later."
        )

    await enqueue_verification_email(db, settings, user)
    await db.commit()
    return AcceptedOut(status="accepted")


@router.post("/email/verify/confirm", response_model=UserOut, dependencies=ANONYMOUS)
async def confirm_email_verification(body: EmailVerifyConfirmIn, db: DbSession) -> User:
    """Verify an address with the token from the emailed link.

    No session is needed: the link is often opened in a browser that is not signed in, and the
    token alone proves the inbox. Unknown, used, expired and wrong-kind tokens all answer the
    same 404.
    """
    user = await consume_token(db, body.token, EmailTokenKind.VERIFY)
    if user is None:
        raise not_found()
    if user.email_verified_at is None:
        user.email_verified_at = utcnow()
    await db.commit()
    return user


@router.post(
    "/password/forgot",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=AcceptedOut,
    dependencies=ANONYMOUS,
)
async def forgot_password(
    body: PasswordForgotIn, request: Request, db: DbSession, settings: SettingsDep
) -> AcceptedOut:
    """Email a reset link if the address has an account. The answer never says whether it does.

    A known and an unknown address take the same path and get the same response: both are
    counted by the limits first, and neither is turned away early. The only difference is that
    a known address also gets a token and a queued email. The demo account counts as unknown.
    """
    # Not configured is the operator's state and is the same for every address.
    _require_email_configured(settings)
    retry_after = await check_forgot_limits(db, body.email, client_ip(request))
    if retry_after is not None:
        raise too_many_requests(
            math.ceil(retry_after), "Too many password reset requests. Try again later."
        )

    user = await db.scalar(select(User).where(User.email == body.email))
    if user is not None and not is_demo_user(user):
        await enqueue_reset_email(db, settings, user)
    await db.commit()
    return AcceptedOut(status="accepted")


@router.post("/password/reset", status_code=status.HTTP_204_NO_CONTENT, dependencies=ANONYMOUS)
async def reset_password(body: PasswordResetIn, request: Request, db: DbSession) -> None:
    """Set a new password with the token from the emailed link.

    No session is needed (the person forgot their password) and none is created: they sign in
    afterwards with the new password. Every session of the account is ended. Unknown, used,
    expired and wrong-kind tokens all answer the same 404.
    """
    if not await complete_password_reset(db, body.token, body.password, ip=client_ip(request)):
        raise not_found()
    await db.commit()
