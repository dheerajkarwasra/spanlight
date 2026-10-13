"""Housekeeping for auth and throttle tables, idempotency keys, rate limit buckets, jobs, outbox
and the explain budget's stale reservations."""

from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import delete, or_
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import (
    EmailToken,
    IdempotencyKey,
    Job,
    JobStatus,
    LoginAttempt,
    NotificationOutbox,
    NotificationStatus,
    RateLimitBucket,
    Session,
    ThrottleEvent,
)
from app.exports.expiry import expire_exports, reap_exports
from app.insights.job_queries import complete_stale_reservations
from app.jobs.context import TaskContext
from app.storage.object_store import get_object_store

logger = structlog.get_logger(__name__)

LOGIN_ATTEMPT_RETENTION = timedelta(days=1)  # the throttle only looks back 15 minutes
THROTTLE_EVENT_RETENTION = timedelta(days=1)  # the longest throttle window is an hour
EMAIL_TOKEN_RETENTION = timedelta(days=1)
FINISHED_JOB_RETENTION = timedelta(days=7)
FINISHED_OUTBOX_RETENTION = timedelta(days=7)
RATE_LIMIT_BUCKET_RETENTION = timedelta(hours=1)  # a bucket idle this long is full again anyway
# An explanation call is answered within the route's 60 s timeout, so a reservation this old
# belongs to a request that died before it could complete or release it. It is completed at its
# worst-case cost, never deleted: the provider may have billed it.
STALE_RESERVATION_AGE = timedelta(minutes=10)


async def run_cleanup_sessions(context: TaskContext, _: dict[str, Any]) -> None:
    now = datetime.now(UTC)
    counts = await cleanup(context.session_factory, now=now)
    # Separate from `cleanup`: it needs object storage, and the exports table is row-level secured.
    store = get_object_store(context.settings)
    counts["exports_expired"] = await expire_exports(context.session_factory, store, now=now)
    counts["exports_reaped"] = await reap_exports(context.session_factory, store, now=now)
    # Separate as well: `insight_explanations` is row-level secured.
    counts["explain_reservations_completed"] = await complete_stale_reservations(
        context.session_factory, before=now - STALE_RESERVATION_AGE, now=now
    )
    logger.info("cleanup_done", **counts)


async def cleanup(
    session_factory: async_sessionmaker[AsyncSession], *, now: datetime
) -> dict[str, int]:
    async with session_factory() as db:
        sessions = await db.execute(
            delete(Session).where(
                or_(Session.expires_at <= now, Session.absolute_expires_at <= now)
            )
        )
        attempts = await db.execute(
            delete(LoginAttempt).where(LoginAttempt.created_at < now - LOGIN_ATTEMPT_RETENTION)
        )
        throttle_events = await db.execute(
            delete(ThrottleEvent).where(ThrottleEvent.created_at < now - THROTTLE_EVENT_RETENTION)
        )
        # A token that can still be used is never pruned. One that is spent or expired is kept
        # for a day, so a replayed link still reads as used for a while.
        email_tokens = await db.execute(
            delete(EmailToken).where(
                or_(EmailToken.used_at.is_not(None), EmailToken.expires_at < now),
                EmailToken.created_at < now - EMAIL_TOKEN_RETENTION,
            )
        )
        # Past `expires_at` a key is ignored by the API already; this only frees the space. That
        # includes a key left in flight by a request that died, which is long past its minute.
        idempotency_keys = await db.execute(
            delete(IdempotencyKey).where(IdempotencyKey.expires_at <= now)
        )
        # An idle bucket has refilled completely (the slowest one takes 1 hour), so dropping it
        # changes nothing for its caller and keeps the table to recent callers.
        rate_limit_buckets = await db.execute(
            delete(RateLimitBucket).where(
                RateLimitBucket.updated_at < now - RATE_LIMIT_BUCKET_RETENTION
            )
        )
        jobs = await db.execute(
            delete(Job).where(
                Job.status.in_([JobStatus.DONE, JobStatus.FAILED]),
                Job.created_at < now - FINISHED_JOB_RETENTION,
            )
        )
        # Pending rows are undelivered mail: never pruned, however old.
        outbox = await db.execute(
            delete(NotificationOutbox).where(
                NotificationOutbox.status.in_([NotificationStatus.SENT, NotificationStatus.FAILED]),
                NotificationOutbox.created_at < now - FINISHED_OUTBOX_RETENTION,
            )
        )
        await db.commit()
    return {
        "sessions_deleted": int(getattr(sessions, "rowcount", 0)),
        "login_attempts_deleted": int(getattr(attempts, "rowcount", 0)),
        "throttle_events_deleted": int(getattr(throttle_events, "rowcount", 0)),
        "email_tokens_deleted": int(getattr(email_tokens, "rowcount", 0)),
        "idempotency_keys_deleted": int(getattr(idempotency_keys, "rowcount", 0)),
        "rate_limit_buckets_deleted": int(getattr(rate_limit_buckets, "rowcount", 0)),
        "jobs_deleted": int(getattr(jobs, "rowcount", 0)),
        "outbox_rows_deleted": int(getattr(outbox, "rowcount", 0)),
    }
