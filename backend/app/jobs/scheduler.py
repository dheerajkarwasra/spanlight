"""Periodic job scheduling via dedupe keys.

Each period gets a deterministic key (e.g. `retention:2026-10-07T13:00`), so any
number of workers can call `schedule_periodic` and each period's job is
enqueued exactly once.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.jobs.queue import enqueue


@dataclass(frozen=True)
class PeriodicJob:
    kind: str
    every: timedelta
    max_attempts: int = 5
    # How long after its period starts the job becomes runnable (zero: at once).
    run_after_offset: timedelta = timedelta(0)


RETENTION = PeriodicJob("retention", timedelta(hours=1))
CLEANUP_SESSIONS = PeriodicJob("cleanup_sessions", timedelta(hours=1))
# Deletes expired gateway cache entries and old per-key minute counters.
PRUNE_GATEWAY_CACHE = PeriodicJob("prune_gateway_cache", timedelta(hours=1))
# Sends every due notification; the outbox does its own per-row retries.
DELIVER_NOTIFICATIONS = PeriodicJob("deliver_notifications", timedelta(seconds=30))
# Rebuilds the last 48 hours of rollups; a run that fails is simply replaced by the next one.
ROLLUP_HOURLY = PeriodicJob("rollup_hourly", timedelta(minutes=5))
# A single attempt: retrying would spend real money on a failed run.
DEMO_TRAFFIC = PeriodicJob("demo_traffic", timedelta(minutes=30), max_attempts=1)
# Evaluates every enabled alert rule. A single attempt: the next pass a minute later is the retry.
EVALUATE_ALERTS = PeriodicJob("evaluate_alerts", timedelta(seconds=60), max_attempts=1)
# One per UTC day, runnable from 03:00 UTC. A worker that starts later in the day still enqueues
# that day's job, which then runs at once. Always scheduled: with backups off it ends `done` /
# `skipped_not_configured`, which keeps the skip visible in the job table.
BACKUP_DATABASE = PeriodicJob(
    "backup_database", timedelta(days=1), max_attempts=3, run_after_offset=timedelta(hours=3)
)
# Mails each project's week to its members. Periods start on the Unix epoch, a Thursday, so a
# four day and eight hour offset lands on Monday 08:00 UTC. A late-starting worker still enqueues
# the period's job, which then runs at once. Without an email provider it ends `done` /
# `skipped_not_configured`.
WEEKLY_DIGEST = PeriodicJob(
    "weekly_digest",
    timedelta(days=7),
    max_attempts=3,
    run_after_offset=timedelta(days=4, hours=8),
)

# Runs every detector over every active project. A single attempt: the next pass is the retry.
RUN_DETECTORS = PeriodicJob("run_detectors", timedelta(minutes=15), max_attempts=1)
# Recomputes yesterday's and today's per-user daily stats. A single attempt, like the detectors.
REFRESH_USER_STATS = PeriodicJob("refresh_user_stats", timedelta(minutes=15), max_attempts=1)
# Reaps abandoned judge runs and, with EVALS_ENABLED, queues runs for recently ended traces.
# Always scheduled: test and experiment runs reserve budget even with online evaluation off, so
# their abandoned reservations must still be released. A single attempt: the next cycle's window
# overlaps this one, so it is the retry.
SAMPLE_FOR_EVALUATION = PeriodicJob("sample_for_evaluation", timedelta(seconds=60), max_attempts=1)


def period_start(job: PeriodicJob, now: datetime) -> datetime:
    seconds = int(job.every.total_seconds())
    return datetime.fromtimestamp(int(now.timestamp()) // seconds * seconds, tz=now.tzinfo)


def period_key(job: PeriodicJob, now: datetime) -> str:
    started = period_start(job, now)
    # Minute precision would give a job that runs more often than once a minute one key per
    # minute. Longer periods keep the minute format, so no existing key changes on deploy.
    time_format = "%Y-%m-%dT%H:%M:%S" if job.every < timedelta(minutes=1) else "%Y-%m-%dT%H:%M"
    return f"{job.kind}:{started.strftime(time_format)}"


def periodic_jobs(settings: Settings) -> list[PeriodicJob]:
    jobs = [
        RETENTION,
        CLEANUP_SESSIONS,
        PRUNE_GATEWAY_CACHE,
        DELIVER_NOTIFICATIONS,
        ROLLUP_HOURLY,
        BACKUP_DATABASE,
        SAMPLE_FOR_EVALUATION,
    ]
    if settings.is_demo_enabled:
        jobs.append(DEMO_TRAFFIC)
    if settings.alerts_evaluation_enabled:
        jobs.append(EVALUATE_ALERTS)
    if settings.weekly_digest_enabled:
        jobs.append(WEEKLY_DIGEST)
    if settings.detectors_enabled:
        jobs.append(RUN_DETECTORS)
    if settings.user_stats_enabled:
        jobs.append(REFRESH_USER_STATS)
    return jobs


async def schedule_periodic(db: AsyncSession, settings: Settings, now: datetime) -> list[str]:
    """Enqueue this period's jobs if not already queued. Returns new job kinds. Commits."""
    created = []
    for job in periodic_jobs(settings):
        run_after = period_start(job, now) + job.run_after_offset if job.run_after_offset else None
        if await enqueue(
            db,
            job.kind,
            run_after=run_after,
            dedupe_key=period_key(job, now),
            max_attempts=job.max_attempts,
        ):
            created.append(job.kind)
    await db.commit()
    return created
