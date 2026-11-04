"""Durable bounded recent selection, closed-hour settlement and optional maintenance."""

import asyncio
import uuid
from collections import Counter
from datetime import UTC, datetime
from typing import Literal

import structlog
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.observability import ROLLUP_DURATION
from app.jobs import aggregate_progress as progress
from app.jobs.context import TaskContext
from app.jobs.fencing import lock_live_job
from app.jobs.project_pass import run_one_project
from app.jobs.queue import LeaseLostError
from app.rollups.maintenance import maintain_project
from app.rollups.recent_queries import KIND, Settlement, settle_recent
from app.scores.repair import repair_has_scores

logger = structlog.get_logger(__name__)
# Approved allocation (ruling 2026-10-30), calibrated locally on PG17: the rest of the shared
# start allowance is reserved for maintenance.
RECENT_SECONDS = 20.0


async def _recent_one(
    context: TaskContext,
    project: uuid.UUID,
    now: datetime,
    seconds: float,
    observations: list[Settlement],
) -> int | None:
    async def refresh(db: AsyncSession, project_id: uuid.UUID) -> int:
        await progress.set_limits(db, seconds)
        result = await settle_recent(db, project_id, now)
        if result is not None:
            observations.append(result)
        await lock_live_job(db, context.job)
        return result.rows if result else 0

    return await run_one_project(
        context.session_factory, project, refresh, "rollup_recent", seconds
    )


async def _recent_round(
    context: TaskContext,
    now: datetime,
    start_deadline: float,
    deadline: float,
    counts: Counter[str],
    observations: list[Settlement],
) -> list[uuid.UUID]:
    visited: list[uuid.UUID] = []
    while len(visited) < progress.MAX_CANDIDATES and progress.remaining(start_deadline):
        await progress.heartbeat(context, deadline)
        if not progress.remaining(start_deadline):
            break
        try:
            project = await progress.next_candidate(context, KIND, visited, deadline)
        except LeaseLostError:
            raise
        except (DBAPIError, TimeoutError) as exc:
            logger.warning("rollup_recent_discovery_failed", error_type=type(exc).__name__)
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
        settled: list[Settlement] = []
        rows = await _recent_one(context, project, now, seconds, settled)
        counts["error" if rows is None else "completed" if settled else "deleted"] += 1
        if rows is not None:
            observations.extend(settled)
            for result in settled:
                counts["refreshed_hours"] += result.refreshed_hours
                counts["unavailable_hours"] += result.unavailable_hours
    return visited


async def _maintenance_one(
    context: TaskContext,
    project: uuid.UUID,
    now: datetime,
    kind: Literal["slice", "gap"],
    seconds: float,
) -> int | None:
    async def refresh(db: AsyncSession, project_id: uuid.UUID) -> int:
        await progress.set_limits(db, seconds)
        rows = await maintain_project(db, project_id, now, kind)
        await lock_live_job(db, context.job)
        return rows

    return await run_one_project(
        context.session_factory, project, refresh, f"rollup_{kind}", seconds
    )


async def _maintenance_round(
    context: TaskContext,
    projects: list[uuid.UUID],
    now: datetime,
    start_deadline: float,
    deadline: float,
    counts: Counter[str],
) -> None:
    for project in projects:
        for kind in ("slice", "gap"):
            if not progress.remaining(start_deadline):
                return
            await progress.heartbeat(context, deadline)
            if not progress.remaining(start_deadline):
                return
            seconds = min(progress.TRANSACTION_SECONDS, progress.remaining(deadline))
            rows = await _maintenance_one(context, project, now, kind, seconds)
            counts[f"{kind}_error" if rows is None else f"{kind}_completed"] += 1


async def _scores(
    context: TaskContext, now: datetime, start_deadline: float, deadline: float
) -> None:
    if not progress.remaining(start_deadline):
        return
    await progress.heartbeat(context, deadline)
    if not progress.remaining(start_deadline):
        return
    seconds = min(progress.TRANSACTION_SECONDS, progress.remaining(deadline))
    try:
        async with asyncio.timeout(seconds), context.session_factory() as db:
            try:
                await progress.set_limits(db, seconds)
                repaired = await repair_has_scores(db, now)
                await lock_live_job(db, context.job)
                await db.commit()
            except BaseException:
                await db.invalidate()
                raise
    except LeaseLostError:
        raise
    except Exception as exc:
        logger.error("has_scores_repair_failed", error_type=type(exc).__name__, exc_info=exc)
        return
    if repaired:
        logger.info("has_scores_repaired", traces=repaired)


async def run_recent(context: TaskContext) -> None:
    started = asyncio.get_running_loop().time()
    now = context.clock().astimezone(UTC)
    deadline = started + progress.OUTER_SECONDS
    start_deadline = started + progress.START_SECONDS
    counts: Counter[str] = Counter()
    observations: list[Settlement] = []
    try:
        async with asyncio.timeout_at(deadline):
            visited = await _recent_round(
                context,
                now,
                min(start_deadline, started + RECENT_SECONDS),
                deadline,
                counts,
                observations,
            )
            await _maintenance_round(context, visited, now, start_deadline, deadline, counts)
            await _scores(context, now, start_deadline, deadline)
    finally:
        elapsed = asyncio.get_running_loop().time() - started
        ROLLUP_DURATION.observe(elapsed)
        pending = [row.pending_since for row in observations if row.pending_since is not None]
        logger.info(
            "rollups_recomputed",
            **counts,
            elapsed_seconds=elapsed,
            pending_sample=len(pending),
            oldest_closed_due=min(pending, default=None),
            observation_scope="committed_visited_projects_only",
            observation_incomplete=counts["attempted"] != counts["completed"] + counts["deleted"],
        )
