"""Token-bucket rate limiting shared by every API replica.

A bucket is one row of `rate_limit_buckets`. Taking a token is a single statement, so replicas
never disagree about how many are left, and nothing is kept in process memory.

Two decisions shape the rest of the module:

* **A token is taken on a connection of its own, in its own transaction.** If it were taken on the
  request's session, the row lock would be held until the request committed, which would
  serialise every concurrent request of one key behind the slowest of them, and a request that
  rolled back would hand its token back. A separate short transaction releases the lock as soon
  as the statement is done and makes a spent token stay spent. The connections come from a small
  pool of their own (see `create_app`) because the request still holds one from the main pool.
* **The database clock is the only clock.** Replicas with skewed clocks must not refill a bucket
  at different speeds.

If the database cannot be reached, `enforce_rate_limit` lets the request through and logs a
warning (at most once a minute per scope) and counts the skip in
`spanlight_rate_limit_unavailable_total`: a limiter that is down must not take the API down with it.
"""

import math
import time
from dataclasses import dataclass
from typing import Protocol

import structlog
from fastapi import Request
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import too_many_requests
from app.core.observability import RATE_LIMIT_REJECTIONS, RATE_LIMIT_UNAVAILABLE

logger = structlog.get_logger(__name__)

# A limiter outage affects every request while it lasts, so the warning is logged at most once a
# minute per scope; `spanlight_rate_limit_unavailable_total` counts every skipped check.
_UNAVAILABLE_LOG_INTERVAL_SECONDS = 60.0
_last_unavailable_log: dict[str, float] = {}

# The tokens a bucket would hold now: what it held when it was last written, plus what it has
# refilled since at `rate` per second, capped at `burst`. A write that is ahead of this
# transaction's clock (another replica committed a moment after this one started) counts as no
# time having passed rather than negative time. Written for the target row `b`.
_REFILLED = """LEAST(
    CAST(:burst AS double precision),
    b.tokens + CAST(GREATEST(EXTRACT(EPOCH FROM (now() - b.updated_at)), 0) AS double precision)
        * CAST(:rate AS double precision)
)"""

# One statement, in two parts.
#
# `taken` is the upsert. A key seen for the first time is inserted with a full bucket minus the
# `cost` tokens being taken (`acquire` guarantees `cost <= burst`, so the bucket never goes
# negative). A key that exists is updated to its refilled count minus the cost, but only `WHERE`
# that count covers it; otherwise the row is left untouched and the upsert returns nothing.
# Postgres serialises concurrent upserts of one key on the row lock and re-evaluates the
# `SET` and the `WHERE` against the row as the previous holder left it, and inserts that race on
# a new key are arbitrated by the primary key, so two requests can never take the same token.
# `updated_at` only moves forward. A refused request writes nothing, so the refill keeps counting
# from the last write.
#
# The outer SELECT reports the result: `allowed` is whether the upsert took the tokens, and
# `retry_after` is how long until a bucket in the state this statement started from holds `cost`
# (read from the statement's snapshot, so under contention it can lag the locked row by a few
# milliseconds; it is a hint for the client, not part of the decision).
# The f-string only splices in the constant above; every value is a bound parameter.
_ACQUIRE = text(
    f"""
    WITH taken AS (
        INSERT INTO rate_limit_buckets AS b (key, tokens, updated_at)
        VALUES (:key, CAST(:burst AS double precision) - CAST(:cost AS double precision), now())
        ON CONFLICT (key) DO UPDATE
            SET tokens = {_REFILLED} - CAST(:cost AS double precision),
                updated_at = GREATEST(now(), b.updated_at)
            WHERE {_REFILLED} >= CAST(:cost AS double precision)
        RETURNING 1
    )
    SELECT
        EXISTS (SELECT 1 FROM taken) AS allowed,
        (
            SELECT (CAST(:cost AS double precision) - {_REFILLED}) / CAST(:rate AS double precision)
            FROM rate_limit_buckets AS b
            WHERE b.key = :key
        ) AS retry_after
    """
)


class AsyncRateLimiter(Protocol):
    """Takes `cost` tokens for `key`, using the session it is given."""

    async def acquire(self, db: AsyncSession, key: str, cost: int = 1) -> float | None:
        """None when the tokens were taken, else the seconds until they will be available."""
        ...


@dataclass(frozen=True)
class PostgresTokenBucket:
    """A token bucket: `rate` tokens per second refill, up to `burst` tokens, state in Postgres.

    `acquire` runs one statement and neither commits nor rolls back. Pass it a session that is
    not the request's (see `enforce_rate_limit`).
    """

    rate: float
    burst: int

    def __post_init__(self) -> None:
        if self.rate <= 0 or self.burst < 1:
            raise ValueError("rate must be positive and burst at least 1")

    async def acquire(self, db: AsyncSession, key: str, cost: int = 1) -> float | None:
        # A first take inserts `burst - cost` with no `WHERE`: a cost above the burst would mint
        # a negative bucket, so it is a caller's bug and fails loudly.
        if cost < 1 or cost > self.burst:
            raise ValueError(f"cost must be between 1 and the burst ({self.burst}), got {cost}")
        result = await db.execute(
            _ACQUIRE, {"key": key, "rate": self.rate, "burst": self.burst, "cost": cost}
        )
        allowed, retry_after = result.one()
        if allowed:
            return None
        # The bucket was just refused, so some wait is owed even if the snapshot the estimate was
        # computed from had already refilled (or the row was pruned in between).
        minimum = cost / self.rate
        return max(float(retry_after), minimum) if retry_after is not None else minimum


INGEST_LIMITER = PostgresTokenBucket(rate=50, burst=100)
"""Per API key on the ingestion routes: 50 requests a second, bursts of 100."""

DEMO_LIMITER = PostgresTokenBucket(rate=10 / 3600, burst=10)
"""Per client address on the demo sign-in: 10 sessions, refilling at 10 an hour."""

API_READ_LIMITER = PostgresTokenBucket(rate=20, burst=40)
"""Per access token or API key on bearer reads of `/api/v1`: 20 a second, bursts of 40."""

BILLING_REFRESH_LIMITER = PostgresTokenBucket(rate=1 / 300, burst=2)
"""Per billing source on its manual refresh: one every 5 minutes, bursts of 2."""

BILLING_UPLOAD_LIMITER = PostgresTokenBucket(rate=6 / 3600, burst=6)
"""Per billing source on its CSV upload: six an hour, so a loop of 5 MB files cannot keep the
threadpool parsing and the rollups recomputing."""

UNSUBSCRIBE_LIMITER = PostgresTokenBucket(rate=1, burst=60)
"""Per client address on the one-click digest unsubscribe: 60 at once, then one a second. Mail
providers send one-click POSTs for many users from few addresses, so the bucket is wide; it
bounds write load, not guessing (a token is a 256-bit HMAC)."""

BILLING_KEY_CHECK_LIMITER = PostgresTokenBucket(rate=1 / 60, burst=5)
"""Per organization on the admin key check of a billing source's create or rotate: one a minute,
bursts of 5, so the check cannot be looped to test keys against a provider."""


async def take_token(
    session_factory: async_sessionmaker[AsyncSession],
    limiter: AsyncRateLimiter,
    key: str,
    *,
    scope: str,
    cost: int = 1,
) -> float | None:
    """Take `cost` tokens from `limiter` for `key`: None when taken, else the seconds to wait.

    The tokens are taken in a short transaction of its own from `session_factory` (the rate-limit
    pool), committed before this returns. If that fails (the pool is exhausted, the database is
    down) the check is skipped, so None, and a warning is logged. `scope` labels the metric and
    the warning. Counting a refusal is the caller's job, since only the caller knows it refused.
    """
    try:
        async with session_factory() as db, db.begin():
            return await limiter.acquire(db, key, cost)
    except SQLAlchemyError as exc:
        RATE_LIMIT_UNAVAILABLE.labels(scope).inc()
        now = time.monotonic()
        last = _last_unavailable_log.get(scope)
        if last is None or now - last >= _UNAVAILABLE_LOG_INTERVAL_SECONDS:
            _last_unavailable_log[scope] = now
            logger.warning("rate_limit_unavailable", scope=scope, error_type=type(exc).__name__)
        return None


async def enforce_rate_limit(
    request: Request,
    limiter: AsyncRateLimiter,
    key: str,
    *,
    scope: str,
    detail: str,
    cost: int = 1,
) -> None:
    """Take `cost` tokens from `limiter` for `key`, or raise 429 `RATE_LIMITED` with `Retry-After`.

    `scope` labels the rejection metric (`ingest`, `ingest_project`, `demo`, `api`,
    `billing_refresh`, `billing_upload`, `billing_key_check` or `unsubscribe`). The tokens are
    taken as `take_token` describes, so a limiter that cannot run lets the request through.
    """
    session_factory = request.app.state.rate_limit_session_factory
    retry_after = await take_token(session_factory, limiter, key, scope=scope, cost=cost)
    if retry_after is not None:
        RATE_LIMIT_REJECTIONS.labels(scope).inc()
        raise too_many_requests(math.ceil(retry_after), detail)
