"""Re-proving that the person behind a session owns the account, before a sensitive change.

A stolen session cookie alone must not be enough to put a second factor in front of the account,
move its email elsewhere or delete it. The proof is the current password, or, for an account
without one, a sign-in this recent; and, when the account has two-factor authentication on, a
code from the authenticator app or an unused recovery code as well.
"""

import math
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.principals import SessionPrincipal
from app.auth import totp_service
from app.config import Settings
from app.core.crypto import CryptoNotConfigured
from app.core.errors import ProblemError, not_configured, too_many_requests
from app.core.security import verify_password_async
from app.core.throttle import check_and_record

REAUTH_WINDOW = timedelta(minutes=10)
# Password and code guesses are limited per user and per caller's scope; every attempt counts.
MAX_REAUTH_ATTEMPTS = 5
PASSWORD_ATTEMPT_WINDOW = timedelta(minutes=15)


async def confirm_account_owner(
    db: AsyncSession,
    auth: SessionPrincipal,
    password: str | None,
    code: str | None,
    *,
    scope: str,
    settings: Settings,
    now: datetime,
) -> None:
    """Refuse unless the caller re-proves the account: `403 REAUTH_REQUIRED` for an account
    without a password whose sign-in is too old, `422 INVALID_PASSWORD`, `422 TOTP_CODE_REQUIRED`
    or `422 INVALID_TOTP_CODE` for an account with two-factor authentication on, or `429` past
    the limit. A valid code is spent.

    `scope` names the throttle counter, so each sensitive action keeps its own. The second factor
    is asked whenever the account has two-factor authentication on.
    """
    user = auth.user
    if user.password_hash is None and datetime.now(UTC) - auth.session.created_at > REAUTH_WINDOW:
        raise ProblemError(
            403, "REAUTH_REQUIRED", "Sign in again, then try again within 10 minutes."
        )
    if user.password_hash is None and not user.totp_enabled:
        return
    retry_after = await check_and_record(
        db, scope, str(user.id), MAX_REAUTH_ATTEMPTS, PASSWORD_ATTEMPT_WINDOW
    )
    if retry_after is not None:
        raise too_many_requests(math.ceil(retry_after), "Too many attempts. Try again later.")
    # Each refusal below commits first: the attempt must stay counted even though the request fails.
    if user.password_hash is not None and (
        password is None or not await verify_password_async(user.password_hash, password)
    ):
        await db.commit()
        raise ProblemError(422, "INVALID_PASSWORD", "That password is not correct.")
    if not user.totp_enabled:
        return
    if code is None:
        await db.commit()
        raise ProblemError(
            422,
            "TOTP_CODE_REQUIRED",
            "Enter the code from your authenticator app, or a recovery code.",
        )
    try:
        # Locks the user row; account delete locks the same row next, so the order is unchanged.
        accepted = await totp_service.verify(db, settings, user, code, now=now)
    except CryptoNotConfigured:
        raise not_configured(
            "Two-factor authentication needs CREDENTIALS_KEYS to be set."
        ) from None
    if not accepted:
        await db.commit()
        raise ProblemError(
            422, "INVALID_TOTP_CODE", "That code is not valid. Check the code and try again."
        )
