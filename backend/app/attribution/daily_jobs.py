"""Bounded daily dispatch and one-day atomic execution under a live queue fence."""

import asyncio
import uuid
from collections import Counter
from datetime import date
from typing import Any

import structlog
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.attribution.daily_periods import (
    DAILY_KIND,
    DAY,
    EXPIRED,
    REFRESHED,
    UNAVAILABLE,
    DailyPayload,
    expired_day,
    horizon,
)
from app.attribution.daily_queries import (
    dispatch_day,
    expire_idle,
    locked_progress,
    matches,
    retention_cutoff,
    settle_day,
)
from app.attribution.job_queries import promoted_keys_of
from app.attribution.refresh import refresh_attribution
from app.jobs import aggregate_progress as progress
from app.jobs.context import TaskContext
from app.jobs.fencing import lock_live_job
from app.jobs.outcome import JobOutcome
from app.jobs.project_pass import run_one_project
from app.jobs.queue import LeaseLostError
from app.rollups.coverage import complete_day_ranges, lock_aggregate_sources

logger = structlog.get_logger(__name__)


async def _dispatch_project(
    db: AsyncSession,
    project: uuid.UUID,
    context: TaskContext,
    scheduled_horizon: date,
    seconds: float,
    observations: list[date],
) -> int:
    await progress.set_limits(db, seconds)
    if not await lock_aggregate_sources(db, project):
        await lock_live_job(db, context.job)
        return 0
    row = await locked_progress(db, project)
    if row.settled_period is None:
        raise RuntimeError("Daily admission is missing its settled sentinel")
    if row.settled_period < scheduled_horizon:
        observations.append(row.active_period or row.settled_period + DAY)
    expire_idle(row, await retention_cutoff(db, project), scheduled_horizon)
    dispatched = await dispatch_day(db, row, scheduled_horizon)
    await db.flush()
    await lock_live_job(db, context.job)
    return int(dispatched)


async def run_schedule_attribution_daily(
    context: TaskContext, payload: dict[str, Any]
) -> JobOutcome | None:
    started = asyncio.get_running_loop().time()
    scheduled_horizon = horizon(payload)
    if not context.settings.attribution_enabled:
        return JobOutcome.SKIPPED_NOT_CONFIGURED
    observations: list[date] = []
    counts: Counter[str] = Counter()
    deadline = started + progress.OUTER_SECONDS
    async with asyncio.timeout_at(deadline):
        await _dispatch_round(context, scheduled_horizon, started, deadline, observations, counts)
        logger.info(
            "attribution_daily_scheduled",
            **counts,
            elapsed_seconds=asyncio.get_running_loop().time() - started,
            pending_sample=len(observations),
            oldest_due_period=min(observations, default=None),
            observation_scope="attempted_candidates_only",
        )
    return None


async def _dispatch_round(
    context: TaskContext,
    scheduled_horizon: date,
    started: float,
    deadline: float,
    observations: list[date],
    counts: Counter[str],
) -> None:
    visited: list[uuid.UUID] = []
    start_deadline = started + progress.START_SECONDS
    while len(visited) < progress.MAX_CANDIDATES and progress.remaining(start_deadline):
        await progress.heartbeat(context, deadline)
        if not progress.remaining(start_deadline):
            break
        try:
            project = await progress.next_candidate(context, DAILY_KIND, visited, deadline)
        except LeaseLostError:
            raise
        except (DBAPIError, TimeoutError) as exc:
            logger.warning("attribution_daily_discovery_failed", error_type=type(exc).__name__)
            counts["error"] += 1
            break
        if project is None:
            break
        visited.append(project)
        counts["attempted"] += 1
        if not progress.remaining(start_deadline):
            counts["deferred"] += 1
            break
        seconds = min(progress.TRANSACTION_SECONDS, progress.remaining(deadline))

        rows = await _dispatch_one(context, project, scheduled_horizon, seconds, observations)
        counts["error" if rows is None else "dispatched" if rows else "deferred"] += 1


async def _dispatch_one(
    context: TaskContext,
    project: uuid.UUID,
    scheduled_horizon: date,
    seconds: float,
    observations: list[date],
) -> int | None:
    async def dispatch(db: AsyncSession, project_id: uuid.UUID) -> int:
        return await _dispatch_project(
            db, project_id, context, scheduled_horizon, seconds, observations
        )

    return await run_one_project(
        context.session_factory, project, dispatch, "attribution_daily_dispatch", seconds
    )


async def _refresh_day(
    db: AsyncSession,
    project: uuid.UUID,
    context: TaskContext,
    payload: DailyPayload,
    seconds: float,
    outcomes: list[int],
) -> int:
    await progress.set_limits(db, seconds)
    if not await lock_aggregate_sources(db, project):
        await lock_live_job(db, context.job)
        return 0
    row = await locked_progress(db, project)
    if not matches(row, payload):
        await lock_live_job(db, context.job)
        return 0
    cutoff = await retention_cutoff(db, project)
    outcome, written = EXPIRED, 0
    if not expired_day(payload.day, cutoff):
        outcome = UNAVAILABLE
        if await complete_day_ranges(db, project, [payload.day]):
            result = await refresh_attribution(
                db,
                project,
                await promoted_keys_of(db, project) or [],
                [payload.day],
                backfill_days=None,
            )
            outcome, written = REFRESHED, result.written
    settle_day(row, payload, outcome)
    await db.flush()
    outcomes.append(outcome)
    await lock_live_job(db, context.job)
    return written


async def run_refresh_attribution_daily(
    context: TaskContext, payload: dict[str, Any]
) -> JobOutcome | None:
    started = asyncio.get_running_loop().time()
    day = DailyPayload.parse(payload)
    if not context.settings.attribution_enabled:
        return JobOutcome.SKIPPED_NOT_CONFIGURED
    deadline = started + progress.OUTER_SECONDS
    outcomes: list[int] = []
    async with asyncio.timeout_at(deadline):
        await progress.heartbeat(context, deadline)
        seconds = min(progress.TRANSACTION_SECONDS, progress.remaining(deadline))

        async def refresh(db: AsyncSession, project: uuid.UUID) -> int:
            return await _refresh_day(db, project, context, day, seconds, outcomes)

        rows = await run_one_project(
            context.session_factory, day.project_id, refresh, "attribution_daily", seconds
        )
        if rows is None:
            raise RuntimeError("Daily attribution transaction did not complete")
        logger.info(
            "attribution_daily_settled",
            scheduled_period=day.scheduled_period,
            day=day.day,
            outcome={REFRESHED: "refreshed", UNAVAILABLE: "unavailable", EXPIRED: "expired"}.get(
                outcomes[0] if outcomes else 0, "deferred"
            ),
            rows=rows,
            elapsed_seconds=asyncio.get_running_loop().time() - started,
        )
    return None
