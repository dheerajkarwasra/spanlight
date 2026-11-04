"""Bounded correction repair, independent of recent-window refresh and retention age."""

import asyncio
import time
from collections.abc import Sequence
from typing import Any

import structlog
from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.attribution.job_queries import promoted_keys_of
from app.attribution.refresh import refresh_attribution
from app.config import Settings
from app.core.observability import (
    AGGREGATE_PENDING_AGE,
    AGGREGATE_PENDING_DEPTH,
    AGGREGATE_REPAIR_OUTCOMES,
    AGGREGATE_RUNNABLE_DEPTH,
)
from app.end_users.refresh import refresh_user_stats
from app.jobs.context import TaskContext
from app.jobs.queue import LeaseLostError
from app.reconciliation.rollups import refresh_cost_rollups
from app.rollups.compute import compute_rollups
from app.rollups.coverage import HOUR, complete_day_ranges, complete_ranges, lock_aggregate_sources
from app.rollups.dirty import (
    ATTRIBUTION,
    COST_DAILY,
    HOURLY,
    USER_DAILY,
    ack_dirty_hour,
    complete_dirty_consumers,
)
from app.rollups.dirty_candidates import Candidate, Scan, backlog

logger = structlog.get_logger(__name__)
START_BUDGET_SECONDS = 30.0
HARD_STOP_SECONDS = 40.0
MARKER_TIMEOUT_SECONDS = 5.0
MAX_ATTEMPTS = 100


def enabled_consumers(settings: Settings) -> int:
    return (
        HOURLY
        | COST_DAILY
        | (ATTRIBUTION if settings.attribution_enabled else 0)
        | (USER_DAILY if settings.user_stats_enabled else 0)
    )


async def run_repair_aggregates(context: TaskContext, _: dict[str, Any]) -> None:
    """Cancellation is cooperative; PostgreSQL timeouts bound statements and lock waits too."""
    started = time.monotonic()
    enabled = enabled_consumers(context.settings)
    try:
        async with asyncio.timeout(HARD_STOP_SECONDS):
            if context.settings.aggregate_repair_enabled:
                await _repair_pass(context, enabled, started + START_BUDGET_SECONDS)
            await _observe_backlog(context, enabled)
    except TimeoutError:
        logger.warning("aggregate_repair_hard_stop", elapsed_seconds=time.monotonic() - started)
        AGGREGATE_REPAIR_OUTCOMES.labels(outcome="error").inc()


async def _observe_backlog(context: TaskContext, enabled: int) -> None:
    async with context.session_factory() as db:
        await db.execute(text("SET LOCAL statement_timeout = '5s'"))
        depth, age, runnable = await backlog(db, enabled)
    AGGREGATE_PENDING_DEPTH.set(depth)
    AGGREGATE_PENDING_AGE.set(age)
    AGGREGATE_RUNNABLE_DEPTH.set(runnable)
    logger.info(
        "aggregate_repair_backlog",
        pending=depth,
        oldest_age_seconds=age,
        runnable=runnable,
        consumption_enabled=context.settings.aggregate_repair_enabled,
    )


async def _repair_pass(context: TaskContext, enabled: int, deadline: float) -> None:
    scan = Scan()
    # Failed fetches also consume an attempt, so a broken scan cannot spin until the deadline.
    for _ in range(MAX_ATTEMPTS):
        if time.monotonic() >= deadline:
            break
        await context.heartbeat()
        if time.monotonic() >= deadline:
            break
        candidate = None
        try:
            async with context.session_factory() as db:
                await db.execute(text("SET LOCAL statement_timeout = '5s'"))
                candidate = await scan.next(db, enabled)
                if candidate is None and scan.round_projects:
                    scan.next_round()
                    candidate = await scan.next(db, enabled)
            if candidate is None or time.monotonic() >= deadline:
                break
            await context.heartbeat()
            if time.monotonic() >= deadline:
                break
            async with asyncio.timeout(MARKER_TIMEOUT_SECONDS), context.session_factory() as db:
                await db.execute(text("SET LOCAL lock_timeout = '250ms'"))
                await db.execute(text("SET LOCAL statement_timeout = '5s'"))
                outcomes = await _repair_marker(db, candidate, enabled)
                await db.commit()
            for outcome in outcomes:
                AGGREGATE_REPAIR_OUTCOMES.labels(outcome=outcome).inc()
        except LeaseLostError:
            raise
        except Exception as exc:
            # Cancellation/lease loss escapes; ordinary failures leave work for the next job.
            if candidate is not None:
                scan.failed_projects.add(candidate.project_id)
            AGGREGATE_REPAIR_OUTCOMES.labels(outcome="error").inc()
            logger.error("aggregate_repair_failed", error_type=type(exc).__name__, exc_info=exc)


async def _repair_marker(db: AsyncSession, candidate: Candidate, enabled: int) -> set[str]:
    project, hour = candidate.project_id, candidate.hour
    if not await lock_aggregate_sources(db, project):
        return set()
    rows = (
        await db.execute(
            text("""
                SELECT -1 AS shard, generation, pending_consumers FROM aggregate_dirty_hours
                WHERE project_id=:project AND bucket_hour=:hour
                UNION ALL
                SELECT shard, generation, pending_consumers FROM aggregate_dirty_shards
                WHERE project_id=:project AND bucket_hour=:hour
                ORDER BY shard
            """),
            {"project": project, "hour": hour},
        )
    ).all()
    if not rows:
        return set()
    pending = 0
    for row in rows:
        pending |= row.pending_consumers
    runnable, unavailable = await _rebuild_hour(db, candidate, pending, enabled)
    settled = runnable | unavailable
    outcomes = set()
    if runnable:
        outcomes.add("repaired")
    if unavailable:
        outcomes.add("unavailable")
    if await _settle_markers(db, candidate, rows, settled):
        outcomes.add("deferred")
    return outcomes


async def _settle_markers(
    db: AsyncSession, candidate: Candidate, rows: Sequence[Row[tuple[int, int, int]]], settled: int
) -> bool:
    project, hour = candidate.project_id, candidate.hour
    deferred = False
    for row in rows:
        completed = row.pending_consumers & settled
        if completed and not await complete_dirty_consumers(
            db, project, hour, row.generation, completed, shard=row.shard
        ):
            deferred = True
        if not await ack_dirty_hour(db, project, hour, row.generation, shard=row.shard):
            deferred = True
    # New rows can commit during rebuilding without changing any observed generation.
    return deferred or bool(
        await db.scalar(
            text("""
            SELECT EXISTS (
                SELECT 1 FROM aggregate_dirty_hours
                WHERE project_id=:project AND bucket_hour=:hour AND pending_consumers <> 0
                UNION ALL
                SELECT 1 FROM aggregate_dirty_shards
                WHERE project_id=:project AND bucket_hour=:hour AND pending_consumers <> 0
            )
        """),
            {"project": project, "hour": hour},
        )
    )


async def _rebuild_hour(
    db: AsyncSession, candidate: Candidate, pending: int, enabled: int
) -> tuple[int, int]:
    project, hour = candidate.project_id, candidate.hour
    hour_complete = bool(await complete_ranges(db, project, hour, hour + HOUR))
    day_complete = bool(await complete_day_ranges(db, project, [hour.date()]))
    unavailable = pending & ((0 if hour_complete else HOURLY) | (0 if day_complete else 14))
    runnable = pending & enabled & ~unavailable
    if runnable & HOURLY:
        await compute_rollups(db, project, hour, hour + HOUR)
    if runnable & COST_DAILY:
        await refresh_cost_rollups(db, project, [hour.date()])
    if runnable & ATTRIBUTION:
        promoted = await promoted_keys_of(db, project) or []
        await refresh_attribution(db, project, promoted, [hour.date()], backfill_days=())
    if runnable & USER_DAILY:
        await refresh_user_stats(db, project, [hour.date()])
    return runnable, unavailable
