"""The caller's own account, under `/auth`: terms acceptance, the name, the weekly digest, the
email address and deleting the account; and the digest's one-click unsubscribe under `/email`.

Every write here needs a browser session (an access token gets 403 `SESSION_REQUIRED`) and is
refused to the shared demo account. Confirming an email change and unsubscribing are the
exceptions: like a password reset, they are anonymous, and the emailed token is the proof.
"""

import math
from datetime import timedelta
from typing import Annotated

import structlog
from fastapi import APIRouter, Query, Request, Response, status
from sqlalchemy import delete, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import aliased

from app.alerts.unsubscribe import user_for_unsubscribe_token
from app.api.deps import (
    ANONYMOUS,
    ClockDep,
    CurrentSession,
    DbSession,
    SettingsDep,
    client_ip,
    utcnow,
)
from app.api.schemas import AcceptedOut, UserOut
from app.api.schemas.account import (
    AccountDeleteIn,
    EmailChangeConfirmIn,
    EmailChangeIn,
    ProfileIn,
    TermsAcceptIn,
    TermsOut,
)
from app.api.v1.auth import clear_auth_cookies
from app.auth.email_tokens import consume_token, issue_token, retire_unused_tokens
from app.auth.reauth import confirm_account_owner
from app.auth.tokens import revoke_all_tokens
from app.core.errors import (
    ProblemError,
    confirmation_mismatch,
    conflict,
    forbidden,
    not_found,
    too_many_requests,
)
from app.core.ratelimit import UNSUBSCRIBE_LIMITER, enforce_rate_limit
from app.core.security import token_digest
from app.core.throttle import check_and_record
from app.db.models import (
    AuditAction,
    EmailToken,
    EmailTokenKind,
    Membership,
    MembershipRole,
    Organization,
    User,
)
from app.email.templates import render_email_change_confirm, render_email_change_notice
from app.notifications import NotificationKind, enqueue
from app.services.audit import record_user_audit_in_each_org
from app.services.demo import is_demo_user
from app.services.sessions import revoke_all_sessions

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])
email_router = APIRouter(prefix="/email", tags=["auth"])

EMAIL_CHANGE_TOKEN_TTL = timedelta(hours=1)
EMAIL_CHANGE_SCOPE = "email_change"
EMAIL_CHANGE_REAUTH_SCOPE = "email_change_reauth"
MAX_EMAIL_CHANGES_PER_HOUR = 3
EMAIL_CHANGE_WINDOW = timedelta(hours=1)
ACCOUNT_DELETE_REAUTH_SCOPE = "account_delete"


def _email_taken() -> ProblemError:
    return conflict("EMAIL_TAKEN", "An account with this email already exists.")


@router.get("/terms", response_model=TermsOut, dependencies=ANONYMOUS)
async def terms(settings: SettingsDep) -> TermsOut:
    """The terms version this server asks for, for the signed-out signup page."""
    return TermsOut(version=settings.terms_version)


@router.post("/terms/accept", status_code=status.HTTP_204_NO_CONTENT)
async def accept_terms(
    body: TermsAcceptIn, auth: CurrentSession, db: DbSession, settings: SettingsDep
) -> None:
    """Accept the server's current terms. Idempotent: accepting the same version again keeps the
    time it was first accepted, and writes no second audit event."""
    if is_demo_user(auth.user):
        raise forbidden("The demo account cannot accept terms.")
    if settings.terms_version is None:
        raise ProblemError(404, "TERMS_NOT_CONFIGURED", "This server asks for no terms.")
    if body.version != settings.terms_version:
        raise conflict(
            "TERMS_VERSION_MISMATCH", "The terms changed; review the current version and accept it."
        )
    # Conditional, so two accepts at once store one timestamp and one audit event.
    accepted = await db.scalar(
        update(User)
        .where(User.id == auth.user.id, User.terms_version.is_distinct_from(settings.terms_version))
        .values(terms_version=settings.terms_version, terms_accepted_at=utcnow())
        .returning(User.id)
    )
    if accepted is not None:
        await record_user_audit_in_each_org(
            db,
            user_id=auth.user.id,
            action=AuditAction.USER_TERMS_ACCEPT,
            metadata={"version": settings.terms_version},
        )
        logger.info("terms_accepted", user_id=str(auth.user.id), version=settings.terms_version)
    await db.commit()


@router.patch("/me", response_model=UserOut)
async def update_profile(body: ProfileIn, auth: CurrentSession, db: DbSession) -> User:
    """Change the caller's name, or turn their weekly digest off or on (every project)."""
    user = auth.user
    if is_demo_user(user):
        raise forbidden("The demo account cannot be changed.")
    if body.name is not None and body.name != user.name:
        user.name = body.name
        await record_user_audit_in_each_org(db, user_id=user.id, action=AuditAction.USER_UPDATE)
        logger.info("profile_updated", user_id=str(user.id))
    if body.weekly_digest is not None:
        user.weekly_digest_opt_out = not body.weekly_digest
    await db.commit()
    return user


@email_router.post("/unsubscribe", response_model=AcceptedOut, dependencies=ANONYMOUS)
async def unsubscribe(
    request: Request,
    db: DbSession,
    settings: SettingsDep,
    # Unconstrained on purpose: a too-long or malformed token gets the same 200, not a 422.
    token: Annotated[str | None, Query()] = None,
) -> AcceptedOut:
    """Turn the weekly digest off for the user an unsubscribe token was made for.

    RFC 8058 one-click: a mail client POSTs `List-Unsubscribe=One-Click` (form-encoded) to the URL
    in the digest's `List-Unsubscribe` header, token in the query, with no cookie and no Origin.
    The `/unsubscribe` page sends `{"token": …}` as JSON instead, read from its URL fragment. Every
    token, valid, forged or missing, gets the same 200, so the route tells nobody which tokens are
    real; repeating it changes nothing.
    """
    await enforce_rate_limit(
        request,
        UNSUBSCRIBE_LIMITER,
        f"unsubscribe:ip:{client_ip(request) or 'unknown'}",
        scope="unsubscribe",
        detail="Too many unsubscribe requests from this address.",
    )
    if token is None and request.headers.get("content-type", "").startswith("application/json"):
        try:
            body = await request.json()
        except ValueError:
            body = None
        token = body.get("token") if isinstance(body, dict) else None
    user_id = user_for_unsubscribe_token(token, settings) if isinstance(token, str) else None
    if user_id is not None:
        await db.execute(update(User).where(User.id == user_id).values(weekly_digest_opt_out=True))
        await db.commit()
        logger.info("weekly_digest_unsubscribed", user_id=str(user_id))
    return AcceptedOut(status="accepted")


@router.post("/email/change", status_code=status.HTTP_202_ACCEPTED, response_model=AcceptedOut)
async def request_email_change(
    body: EmailChangeIn, auth: CurrentSession, db: DbSession, settings: SettingsDep, clock: ClockDep
) -> AcceptedOut:
    """Mail the new address a link that moves the account to it, and the current one a notice.

    Needs the current password, or a sign-in in the last 10 minutes for an account without one,
    and a two-factor code when two-factor authentication is on. Nothing changes until the link is
    opened; only the newest link works.
    """
    # Not configured first: it is the operator's state, not something about the user.
    if not settings.is_email_configured:
        raise ProblemError(
            503, "EMAIL_NOT_CONFIGURED", "Email is not configured, so the address cannot change."
        )
    user = auth.user
    if is_demo_user(user):
        raise forbidden("The demo account cannot be changed.")
    await confirm_account_owner(
        db,
        auth,
        body.password,
        body.code,
        scope=EMAIL_CHANGE_REAUTH_SCOPE,
        settings=settings,
        now=clock(),
    )
    # Before the taken check, so probing which addresses have accounts is limited too.
    retry_after = await check_and_record(
        db, EMAIL_CHANGE_SCOPE, str(user.id), MAX_EMAIL_CHANGES_PER_HOUR, EMAIL_CHANGE_WINDOW
    )
    if retry_after is not None:
        raise too_many_requests(
            math.ceil(retry_after), "Too many email change requests. Try again later."
        )
    if await db.scalar(select(User.id).where(User.email == body.new_email)) is not None:
        # The attempts above must stay counted.
        await db.commit()
        raise _email_taken()

    await retire_unused_tokens(db, user.id, EmailTokenKind.EMAIL_CHANGE)
    raw_token = await issue_token(
        db, user.id, EmailTokenKind.EMAIL_CHANGE, EMAIL_CHANGE_TOKEN_TTL, new_email=body.new_email
    )
    # The token is in the fragment, like a reset link, so it stays out of access logs.
    url = f"{settings.app_base_url}/confirm-email-change#token={raw_token}"
    for to, message in (
        (body.new_email, render_email_change_confirm(user.name, url)),
        (user.email, render_email_change_notice(user.name)),
    ):
        await enqueue(
            db,
            NotificationKind.EMAIL,
            target={"to": to},
            payload={"subject": message.subject, "text": message.text, "html": message.html},
        )
    await db.commit()
    logger.info("email_change_requested", user_id=str(user.id))
    return AcceptedOut(status="accepted")


@router.post(
    "/email/change/confirm", status_code=status.HTTP_204_NO_CONTENT, dependencies=ANONYMOUS
)
async def confirm_email_change(body: EmailChangeConfirmIn, request: Request, db: DbSession) -> None:
    """Move the account to the address the emailed link was sent to, end every session and revoke
    every personal access token.

    No session is needed: the token proves the new inbox. Unknown, used, expired and wrong-kind
    tokens all answer the same 404 as a reset does. Sign-in providers stay linked.
    """
    user = await consume_token(db, body.token, EmailTokenKind.EMAIL_CHANGE)
    if user is None:
        raise not_found()
    new_email = await db.scalar(
        select(EmailToken.new_email).where(EmailToken.token_hash == token_digest(body.token))
    )
    if new_email is None:
        raise not_found()
    # A 409 rolls back, so the token stays unspent.
    if await db.scalar(select(User.id).where(User.email == new_email)) is not None:
        raise _email_taken()
    user.email = new_email
    user.email_verified_at = utcnow()
    try:
        # Flushed before the sessions go, as `replace_password` does (`app.services.credentials`).
        await db.flush()
    except IntegrityError as exc:
        raise _email_taken() from exc
    # Links mailed to the old address must not outlive the change.
    for kind in EmailTokenKind:
        await retire_unused_tokens(db, user.id, kind)
    await revoke_all_sessions(db, user.id)
    # As after a password reset: a token minted from a stolen session must not outlive the change.
    tokens_revoked = await revoke_all_tokens(db, user.id, now=utcnow())
    await record_user_audit_in_each_org(
        db,
        user_id=user.id,
        action=AuditAction.USER_EMAIL_CHANGE,
        ip=client_ip(request),
        metadata={"changed": ["email"], "tokens_revoked": tokens_revoked},
    )
    await db.commit()
    logger.info("email_changed", user_id=str(user.id))


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(
    body: AccountDeleteIn,
    auth: CurrentSession,
    db: DbSession,
    settings: SettingsDep,
    clock: ClockDep,
) -> Response:
    """Delete the caller's account for good, and sign this browser out.

    Needs `confirm: "delete"` and the current password (or a sign-in in the last 10 minutes for an
    account without one), plus a two-factor code when two-factor authentication is on. Refused
    with `409 SOLE_OWNER`, listing the organizations, while the caller is the only owner of any.
    Sessions, tokens, sign-in providers, recovery codes and memberships go with the account; what
    the caller made in a project stays, without an author.
    """
    user = auth.user
    if is_demo_user(user):
        raise forbidden("The demo account cannot be deleted.")
    if body.confirm != "delete":
        raise confirmation_mismatch("`confirm` must be the word `delete`.")
    # The code check locks the user row; the lock below takes the same row again, so the order
    # (user row, then memberships) is unchanged.
    await confirm_account_owner(
        db,
        auth,
        body.password,
        body.code,
        scope=ACCOUNT_DELETE_REAUTH_SCOPE,
        settings=settings,
        now=clock(),
    )

    # A membership being inserted for the user (an org created in another tab) holds a key-share
    # lock on this row, so it either commits before the owner check below or fails after the delete.
    await db.execute(select(User.id).where(User.id == user.id).with_for_update())
    # The caller's organizations next, in id order and before any of their rows
    # (`app.services.deletion`): a deletion of one holds its row and cascades into the memberships
    # locked below, while the audit event written below needs a shared lock on that row, so
    # holding the memberships first would deadlock the two. The set is fixed: a membership being
    # added holds a share on the user row locked above.
    await db.execute(
        select(Organization.id)
        .where(Organization.id.in_(select(Membership.org_id).where(Membership.user_id == user.id)))
        .order_by(Organization.id)
        .with_for_update(read=True, key_share=True)
    )
    # The caller's memberships and every owner row of the orgs they own, locked in one order
    # before any audit lock, as removing a member does (`orgs._locked_membership`, then the audit):
    # the cascade below then finds its rows already held, and two co-owners deleting at once
    # cannot both go (`orgs._ensure_not_last_owner`).
    mine = aliased(Membership)
    owned = select(mine.org_id).where(mine.user_id == user.id, mine.role == MembershipRole.OWNER)
    locked = (
        await db.execute(
            select(Membership.org_id, Membership.user_id, Membership.role)
            .where(
                or_(
                    Membership.user_id == user.id,
                    Membership.org_id.in_(owned) & (Membership.role == MembershipRole.OWNER),
                )
            )
            .order_by(Membership.org_id, Membership.user_id)
            .with_for_update()
        )
    ).all()
    owners = [
        (org_id, owner_id) for org_id, owner_id, role in locked if role is MembershipRole.OWNER
    ]
    others = {org_id for org_id, owner_id in owners if owner_id != user.id}
    sole = sorted({org_id for org_id, _ in owners} - others)
    if sole:
        orgs = (
            await db.execute(
                select(Organization.id, Organization.slug, Organization.name)
                .where(Organization.id.in_(sole))
                .order_by(Organization.name, Organization.id)
            )
        ).all()
        raise ProblemError(
            409,
            "SOLE_OWNER",
            "Hand over or delete the organizations you are the only owner of first.",
            extensions={
                "orgs": [{"id": str(org.id), "slug": org.slug, "name": org.name} for org in orgs]
            },
        )

    # Before the row goes, so each event still names the actor and their address.
    await record_user_audit_in_each_org(db, user_id=user.id, action=AuditAction.USER_DELETE)
    await db.execute(delete(User).where(User.id == user.id))
    await db.commit()
    # After the commit, so a rolled-back delete is never reported. Ids only.
    logger.info("account_deleted", user_id=str(user.id))
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_auth_cookies(response, settings)
    return response
