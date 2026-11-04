"""The sign-in failure limit, shared by the password step and the two-factor step.

A wrong password and a wrong one-time code are the same kind of failure to the account: both
are guesses, and both are recorded as a `login_attempts` row with `succeeded` false. Counting
them together means a thief cannot get five tries at the password and five more at the code.

The small limit is per (email, IP), so five guesses from one address lock that address out of
the account without locking the account's owner out from theirs. The larger per-email cap
still stops a guess spread over many addresses.
"""

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ProblemError, too_many_requests
from app.core.throttle import check_and_record
from app.db.models import LoginAttempt

LOGIN_WINDOW = timedelta(minutes=15)
MAX_FAILURES_PER_EMAIL_IP = 5
MAX_FAILURES_PER_EMAIL = 50
MAX_FAILURES_PER_IP = 20
MAX_HASHES_PER_EMAIL_IP = 20
THROTTLED_MESSAGE = "Too many failed sign-in attempts. Try again later."


async def enforce_login_throttle(
    db: AsyncSession, *, email: str, ip: str | None, now: datetime
) -> None:
    """Block once an (email, IP) has 5, an email 50, or an IP 20, failures in the last 15 minutes.

    With the client address unknown, the limit of 5 applies to the email itself.

    Call it in the transaction that records the attempt. It takes an advisory lock on the email,
    held until that transaction ends, so parallel guesses at one account are counted and recorded
    one after another and the limit is exact. The IP limit is not locked: serialising every
    sign-in from one office address through password hashing costs more than its bounded overshoot.
    """
    # `email` is CITEXT in both tables, so every spelling of an address counts against one
    # account; the lock key is folded the same way or mixed-case guesses would not queue.
    await db.execute(
        select(func.pg_advisory_xact_lock(func.hashtextextended(f"login:{email.lower()}", 0)))
    )

    failures = select(func.count(), func.min(LoginAttempt.created_at)).where(
        LoginAttempt.succeeded.is_(False), LoginAttempt.created_at > now - LOGIN_WINDOW
    )
    by_email = failures.where(LoginAttempt.email == email)
    if ip is None:
        limits = [(by_email, MAX_FAILURES_PER_EMAIL_IP)]
    else:
        limits = [
            (by_email.where(LoginAttempt.ip == ip), MAX_FAILURES_PER_EMAIL_IP),
            (by_email, MAX_FAILURES_PER_EMAIL),
            (failures.where(LoginAttempt.ip == ip), MAX_FAILURES_PER_IP),
        ]
    for query, limit in limits:
        count, oldest = (await db.execute(query)).one()
        if count >= limit:
            raise _throttled(oldest, now)


async def enforce_hash_budget(db: AsyncSession, *, email: str, ip: str | None) -> None:
    """Allow at most 20 password hashes per (email, IP) in the window, counted before hashing.

    The failure count above only sees recorded attempts, so a burst that arrives before its
    first failures commit passes it whole and every guess costs an argon2 verify. This budget
    is recorded at once, so the burst is bounded. It counts hashes, not failures: a correct
    password counts too. With the client address unknown the budget is per email, like the
    failure limit. Call it in the transaction that commits before hashing.
    """
    wait = await check_and_record(
        db, "login_hash", f"{email.lower()}:{ip}", MAX_HASHES_PER_EMAIL_IP, LOGIN_WINDOW
    )
    if wait is not None:
        raise too_many_requests(int(wait) + 1, THROTTLED_MESSAGE)


def _throttled(oldest_failure: datetime, now: datetime) -> ProblemError:
    retry_after = int((oldest_failure + LOGIN_WINDOW - now).total_seconds()) + 1
    return too_many_requests(retry_after, THROTTLED_MESSAGE)
