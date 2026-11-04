"""The `weekly_digest` job: email each project's week to the members of its organization.

The scheduler enqueues one job per period (see `jobs/scheduler.py`); it becomes runnable on
Monday 08:00 UTC and summarises the seven whole UTC days that ended at Monday 00:00. One query,
with row-level security bypassed, lists the projects that have the digest on and had traffic that
week. Each project is then handled in its own transaction, bound to it alone: its figures and
its Doctor insights (`app.insights.digest`) are read, and one `email` outbox row per verified member
who did not opt out is queued, each with its own unsubscribe link and RFC 8058 one-click headers
(`app.alerts.unsubscribe`).

Idempotency: a job that is retried (it has three attempts) or runs twice must not mail anyone
twice. Every row carries `summary.digest_key = weekly_digest:<project>:<week start>`, which the
outbox keeps after the row is settled. A project's rows are queued in one transaction, under an
advisory lock on that key, and only if no row with the key exists, so a project is either
complete or absent. A run that failed for some projects raises at the end; its retry fills in
exactly those.
"""

import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alerts.digest import render_digest
from app.alerts.digest_queries import (
    DigestTarget,
    digest_already_queued,
    digest_data,
    digest_recipients,
    eligible_projects,
    lock_digest,
    queued_digest_keys,
)
from app.alerts.unsubscribe import unsubscribe_links
from app.api.window import TimeWindow
from app.db.rls import bind_project
from app.insights.digest import insights_digest_section
from app.jobs.context import TaskContext
from app.jobs.outcome import JobOutcome
from app.notifications.outbox import NotificationKind, enqueue

if TYPE_CHECKING:
    from app.config import Settings

logger = structlog.get_logger(__name__)

# The worker cancels a task after 50 s; the project in flight when the budget runs out has the
# remaining 10 s to finish. Projects left over are done by the retry.
RUN_BUDGET_SECONDS = 40.0


@dataclass(slots=True)
class DigestRun:
    projects: int = 0
    queued_projects: int = 0
    rows: int = 0
    already_queued: int = 0
    without_recipients: int = 0
    failed: int = 0
    left: int = 0


class WeeklyDigestIncompleteError(RuntimeError):
    """Some projects were not handled; the job fails so its retry handles them."""


async def run_weekly_digest(context: TaskContext, _: dict[str, Any]) -> JobOutcome | None:
    settings = context.settings
    if not settings.weekly_digest_enabled:
        logger.info("weekly_digest_skipped", reason="WEEKLY_DIGEST_ENABLED is false")
        return JobOutcome.SKIPPED_NOT_CONFIGURED
    if not settings.is_email_configured:
        logger.info("weekly_digest_skipped", reason="no email provider is configured")
        return JobOutcome.SKIPPED_NOT_CONFIGURED
    run = await send_digests(
        context.session_factory, settings, now=datetime.now(UTC), heartbeat=context.heartbeat
    )
    logger.info(
        "weekly_digest_done",
        projects=run.projects,
        queued_projects=run.queued_projects,
        rows=run.rows,
        already_queued=run.already_queued,
        without_recipients=run.without_recipients,
        failed=run.failed,
        left=run.left,
    )
    if run.failed or run.left:
        if context.job.attempts >= context.job.max_attempts:
            logger.error(
                "weekly_digest_incomplete",
                failed=run.failed,
                left=run.left,
                projects=run.projects,
                attempts=context.job.attempts,
            )
        raise WeeklyDigestIncompleteError(
            f"weekly digest incomplete: {run.failed} failed, {run.left} not reached"
        )
    return None


def digest_week(now: datetime) -> TimeWindow:
    """The seven UTC days before the latest Monday 00:00 at or before `now`.

    A retry on Tuesday still summarises the same week as the first attempt on Monday.
    """
    midnight = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    monday = midnight - timedelta(days=midnight.weekday())
    return TimeWindow(start=monday - timedelta(days=7), end=monday)


def digest_key(project_id: uuid.UUID, week: TimeWindow) -> str:
    return f"weekly_digest:{project_id}:{week.start.date().isoformat()}"


@dataclass(slots=True)
class _Pass:
    """What one run shares between its projects."""

    week: TimeWindow
    now: datetime
    settings: "Settings"
    # Digest keys already in the outbox when the run started; grows as projects are queued.
    queued: set[str]
    # Verified members (id, address) per organization: its projects share one read.
    recipients: dict[uuid.UUID, list[tuple[uuid.UUID, str]]] = field(default_factory=dict)


async def send_digests(
    session_factory: async_sessionmaker[AsyncSession],
    settings: "Settings",
    *,
    now: datetime,
    heartbeat: Callable[[], Awaitable[None]] | None = None,
    budget_seconds: float = RUN_BUDGET_SECONDS,
) -> DigestRun:
    """Queue the digests of the week that ended before `now`. `heartbeat` runs between projects."""
    week = digest_week(now)
    deadline = time.monotonic() + budget_seconds
    async with session_factory() as db:
        targets = await eligible_projects(db, week)
        queued = await queued_digest_keys(db, week.end)
    shared = _Pass(week, now, settings, queued)
    run = DigestRun(projects=len(targets))
    for index, target in enumerate(targets):
        if time.monotonic() >= deadline:
            run.left = len(targets) - index
            logger.warning("weekly_digest_out_of_time", projects_left=run.left)
            break
        if heartbeat is not None:
            await heartbeat()
        await _handle_project(session_factory, shared, target, run)
    return run


async def _handle_project(
    session_factory: async_sessionmaker[AsyncSession],
    shared: _Pass,
    target: DigestTarget,
    run: DigestRun,
) -> None:
    """Queue one project's digest and count the outcome; a failure is logged and counted."""
    key = digest_key(target.project_id, shared.week)
    if key in shared.queued:
        run.already_queued += 1
        return
    try:
        async with session_factory() as db:
            rows = await _queue_project(db, shared, target, key)
    except Exception as exc:  # isolate the project; the retry handles it
        run.failed += 1
        logger.error(
            "weekly_digest_project_failed",
            project_id=str(target.project_id),
            error_type=type(exc).__name__,
            exc_info=exc,
        )
        return
    if rows is None:
        run.already_queued += 1
    elif rows == 0:
        run.without_recipients += 1
    else:
        run.queued_projects += 1
        run.rows += rows


async def _queue_project(
    db: AsyncSession, shared: _Pass, target: DigestTarget, key: str
) -> int | None:
    """Queue one project's digest and commit; the number of rows, or None when already queued."""
    await bind_project(db, target.project_id)
    await lock_digest(db, key)
    # Another run may have queued it since the run-start read; the check is bounded by the week.
    if await digest_already_queued(db, key, shared.week.end):
        await db.rollback()
        shared.queued.add(key)
        return None
    recipients = shared.recipients.get(target.org_id)
    if recipients is None:
        recipients = shared.recipients[target.org_id] = await digest_recipients(db, target.org_id)
    if not recipients:
        await db.rollback()
        return 0
    data = await digest_data(
        db, target, shared.week, now=shared.now, app_base_url=shared.settings.app_base_url
    )
    insights = await insights_digest_section(db, target.project_id, shared.week)
    if insights is not None:
        data = replace(data, sections=[*data.sections, insights])
    summary = {"event": "weekly_digest", "digest_key": key, "project_id": str(target.project_id)}
    for user_id, address in recipients:
        unsubscribe_url, headers = unsubscribe_links(user_id, shared.settings)
        message = render_digest(data, unsubscribe_url)
        await enqueue(
            db,
            NotificationKind.EMAIL,
            {"to": address},
            {
                "subject": message.subject,
                "text": message.text,
                "html": message.html,
                "headers": headers,
                "summary": summary,
            },
        )
    await db.commit()
    shared.queued.add(key)
    return len(recipients)
