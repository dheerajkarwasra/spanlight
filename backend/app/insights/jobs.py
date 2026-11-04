"""The Doctor's jobs: `run_detectors` every 15 minutes lists the active projects and enqueues
one `run_detectors_project` job per project; that job runs every detector over its project.

One query, with row-level security bypassed, lists the projects that have a span in the last
24 hours, the project whose detectors ran longest ago first; the queue keeps that order. Each
project job runs in a session of its own, bound to its project alone (see `engine.py`), under
the queue's own lease and fence; all projects of a pass share one `now`, carried in the payload.

Both jobs have a single attempt: the next pass is the retry. A project job is deduplicated per
pass (one job per project per 15-minute period), so a slow pass cannot pile up copies, and a
project's slow scan or failure holds up nothing but its own job. The project jobs of a pass are
due `STAGGER` apart (the whole set within `STAGGER_SPAN`): the worker claims by due time, so the
notification and alert jobs that share its short lane run between the project jobs instead of
behind all of them. A project job whose pass is older than one period is skipped without work:
when the lane falls behind, the backlog stays at most one pass deep, and the next pass covers
the project with a fresh `now`. A project runs under its own time bound inside the worker's
task timeout, so a timed-out project still records an error row for each detector that had not
recorded one; the failure shows in detector health and the project moves to the back of the
order.
"""

import asyncio
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.observability import DETECTOR_PROJECTS_STALE, INSIGHTS_OPEN
from app.insights.engine import (
    DETECTORS,
    DetectorRunResult,
    error_text,
    record_failed_runs,
    run_project,
)
from app.insights.job_queries import active_project_ids, open_insights_by_severity
from app.jobs.context import TaskContext
from app.jobs.queue import enqueue
from app.jobs.scheduler import RUN_DETECTORS, period_key

if TYPE_CHECKING:
    from app.config import Settings

logger = structlog.get_logger(__name__)

PROJECT_JOB = "run_detectors_project"
# The worker cancels a task after 50 s; this leaves time to record a timed-out project's error
# rows and refresh the gauge.
PROJECT_TIMEOUT_SECONDS = 30.0
ACTIVE_WINDOW = timedelta(hours=24)
# Gap between the due times of a pass's project jobs, and the most the last one may trail the
# pass: well inside the 15-minute period, so an on-time pass ends before the next one starts.
STAGGER = timedelta(seconds=5)
STAGGER_SPAN = timedelta(minutes=10)


async def run_detectors(context: TaskContext, _: dict[str, Any]) -> None:
    """List the active projects and enqueue this pass's job for each."""
    now = datetime.now(UTC)
    async with context.session_factory() as db:
        project_ids = await active_project_ids(db, now - ACTIVE_WINDOW, now)
    created = await schedule_projects(context.session_factory, project_ids, now)
    logger.info("detectors_scheduled", projects=len(project_ids), jobs=created)


async def schedule_projects(
    session_factory: async_sessionmaker[AsyncSession], project_ids: list[uuid.UUID], now: datetime
) -> int:
    """One `run_detectors_project` job per project, keyed by the pass's period and due `STAGGER`
    apart in the listing's order; returns how many were new. Commits."""
    period = period_key(RUN_DETECTORS, now)
    step = min(STAGGER, STAGGER_SPAN / max(len(project_ids), 1))
    created = 0
    async with session_factory() as db:
        for index, project_id in enumerate(project_ids):
            created += await enqueue(
                db,
                PROJECT_JOB,
                {"project_id": str(project_id), "now": now.isoformat()},
                run_after=now + index * step,
                dedupe_key=f"{period}:{project_id}",
                max_attempts=1,
            )
        await db.commit()
    return created


async def run_detectors_project(context: TaskContext, payload: dict[str, Any]) -> None:
    """Run every detector over one project at the pass's `now`, then refresh the open gauge.
    A job from a pass older than one period is skipped (logged and counted, never failed)."""
    project_id = uuid.UUID(payload["project_id"])
    now = datetime.fromisoformat(payload["now"])
    age = datetime.now(UTC) - now
    if age > RUN_DETECTORS.every:
        DETECTOR_PROJECTS_STALE.inc()
        logger.warning(
            "detector_project_stale",
            project_id=str(project_id),
            pass_age_s=round(age.total_seconds()),
        )
        return
    results = await _run_one_project(context.session_factory, context.settings, project_id, now)
    await _refresh_open_gauge(context.session_factory)
    if results is None:
        return  # logged and recorded by `_run_one_project`
    logger.info(
        "detector_project_ran",
        project_id=str(project_id),
        project_gone=not results,  # deleted since the pass listed it
        runs=len(results),
        errors=sum(1 for result in results if result.error is not None),
        opened=sum(result.opened for result in results),
        notified=sum(result.notified for result in results),
    )


async def _run_one_project(
    session_factory: async_sessionmaker[AsyncSession],
    settings: "Settings",
    project_id: uuid.UUID,
    now: datetime,
) -> list[DetectorRunResult] | None:
    """The project's run results, or None when the project failed or ran out of
    `PROJECT_TIMEOUT_SECONDS` (logged, and recorded by `_record_project_failure`).

    Detector failures are recorded inside; this only catches a failure around them, such as the
    context read or the connection failing, so the job still ends and records the failure.
    """
    started = time.monotonic()
    progress: list[DetectorRunResult] = []
    try:
        async with asyncio.timeout(PROJECT_TIMEOUT_SECONDS), session_factory() as db:
            return await run_project(
                db, project_id, now, DETECTORS, settings=settings, progress=progress
            )
    except TimeoutError as exc:
        error = f"TimeoutError: the project's run took longer than {PROJECT_TIMEOUT_SECONDS:.0f} s"
        logger.error("detector_project_timed_out", project_id=str(project_id), exc_info=exc)
    except Exception as exc:  # isolate the project; the next pass tries it again
        error = error_text(exc)
        logger.error(
            "detector_project_failed",
            project_id=str(project_id),
            error_type=type(exc).__name__,
            exc_info=exc,
        )
    elapsed_ms = round((time.monotonic() - started) * 1000)
    await _record_project_failure(session_factory, project_id, now, progress, error, elapsed_ms)
    return None


async def _record_project_failure(
    session_factory: async_sessionmaker[AsyncSession],
    project_id: uuid.UUID,
    now: datetime,
    progress: list[DetectorRunResult],
    error: str,
    elapsed_ms: int,
) -> None:
    """Error rows for the detectors of a failed project that had not recorded a run."""
    recorded = {result.detector for result in progress if result.recorded}
    missing = [detector for detector in DETECTORS if detector.kind not in recorded]
    if not missing:
        return
    try:
        async with session_factory() as db:
            await record_failed_runs(db, project_id, now, missing, error, elapsed_ms)
    except Exception as exc:  # e.g. the project was deleted; nothing more to record
        logger.error(
            "detector_project_failure_not_recorded",
            project_id=str(project_id),
            error_type=type(exc).__name__,
            exc_info=exc,
        )


async def _refresh_open_gauge(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """Set `spanlight_insights_open` from one count over every project. A failure only leaves
    the gauge at its previous value."""
    try:
        async with session_factory() as db:
            counts = await open_insights_by_severity(db)
    except Exception as exc:  # the gauge is a convenience; the project's run itself succeeded
        logger.warning("insights_open_gauge_failed", error_type=type(exc).__name__, exc_info=exc)
        return
    for severity, count in counts.items():
        INSIGHTS_OPEN.labels(severity=severity.value).set(count)
