"""Password reset: the link, the email that carries it, and what a successful reset changes.

The routes live in `app.api.v1.auth`; the rules that make them safe live here.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.email_tokens import consume_token, issue_token, retire_unused_tokens
from app.auth.oauth_service import drop_identities
from app.auth.tokens import revoke_all_tokens
from app.config import Settings
from app.core.security import hash_password_async
from app.core.throttle import check_and_record
from app.db.models import AuditAction, EmailTokenKind, User
from app.email.templates import render_password_reset
from app.notifications import NotificationKind, enqueue
from app.services.audit import record_user_audit_in_each_org
from app.services.credentials import replace_password

RESET_TOKEN_TTL = timedelta(hours=1)

FORGOT_EMAIL_SCOPE = "password_forgot_email"
FORGOT_IP_SCOPE = "password_forgot_ip"
MAX_FORGOT_PER_EMAIL = 3
MAX_FORGOT_PER_IP = 10
FORGOT_WINDOW = timedelta(minutes=15)


def reset_url(settings: Settings, raw_token: str) -> str:
    """The link in the email. The token is in the fragment, which browsers send neither to the
    server nor in a `Referer` header, so it stays out of access logs; the page reads it and
    posts it to the reset route.
    """
    return f"{settings.app_base_url}/reset-password#token={raw_token}"


async def check_forgot_limits(db: AsyncSession, email: str, ip: str | None) -> float | None:
    """Count a forgot-password request against its limits; return the wait if it is refused.

    Every request counts, whether or not the address has an account, so hitting a limit
    reveals nothing about accounts. The email is checked before the IP, always in that order:
    each check holds an advisory lock until the transaction ends, and one fixed order means two
    requests can never each wait for the lock the other holds. A request refused by the email
    limit never reaches the IP check, so one hammered address does not use up the IP's budget.
    """
    retry_after = await check_and_record(
        db, FORGOT_EMAIL_SCOPE, email.lower(), MAX_FORGOT_PER_EMAIL, FORGOT_WINDOW
    )
    if retry_after is not None or ip is None:
        return retry_after
    return await check_and_record(db, FORGOT_IP_SCOPE, ip, MAX_FORGOT_PER_IP, FORGOT_WINDOW)


async def enqueue_reset_email(db: AsyncSession, settings: Settings, user: User) -> None:
    """Issue a reset token and queue the email that carries it, in the caller's transaction.

    Links the user was sent earlier stop working first, so at most one reset link is live at a
    time. The token and the outbox row commit together: there is never a link nobody was sent,
    or an email whose link does not work. The caller checks `settings.is_email_configured`.
    """
    await retire_unused_tokens(db, user.id, EmailTokenKind.RESET)
    raw_token = await issue_token(db, user.id, EmailTokenKind.RESET, RESET_TOKEN_TTL)
    message = render_password_reset(user.name, reset_url(settings, raw_token))
    await enqueue(
        db,
        NotificationKind.EMAIL,
        target={"to": user.email},
        payload={"subject": message.subject, "text": message.text, "html": message.html},
    )


async def complete_password_reset(
    db: AsyncSession, raw_token: str, new_password: str, *, ip: str | None
) -> bool:
    """Spend a reset token and set the new password. False, with nothing changed, if it is bad.

    Everything below is one transaction, which the caller commits, so a reset is either complete
    or absent: the token is not spent without the password changing, and no session survives
    a password that changed (including one a racing sign-in is creating; see
    `app.services.credentials`).

    The user is not signed in. Whoever holds the link proved the inbox, not that they are the
    person at the keyboard, and signing out everywhere is the point of a reset.
    """
    # The token is spent before the (deliberately slow) hashing, so a stream of bad tokens
    # cannot be turned into a stream of argon2 hashes.
    user = await consume_token(db, raw_token, EmailTokenKind.RESET)
    if user is None:
        return False

    if user.email_verified_at is None:
        user.email_verified_at = datetime.now(UTC)  # the link reached the inbox, so it is theirs
        # Until now the address was unproven, so whoever registered it, the owner or not, could
        # have attached a sign-in provider that would keep the way in after this recovery. The
        # owner has just proven the address; they can link their own providers again.
        await drop_identities(db, user.id, reason="password_reset", ip=ip)
    await replace_password(db, user, await hash_password_async(new_password))
    await retire_unused_tokens(db, user.id, EmailTokenKind.RESET)
    tokens_revoked = await revoke_all_tokens(db, user.id, now=datetime.now(UTC))
    await record_user_audit_in_each_org(
        db,
        user_id=user.id,
        action=AuditAction.USER_PASSWORD_RESET,
        ip=ip,
        metadata={"via": "email", "tokens_revoked": tokens_revoked},
    )
    return True
