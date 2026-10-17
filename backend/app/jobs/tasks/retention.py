"""Delete traces (and, by cascade, their spans) past each project's retention, with the
project's hourly rollups and telemetry losses (`app.rollups.retention`), resolved insights, old
detector runs, old per-user daily stats, daily cost rollups and daily attribution rollups. The
statements are in `app.jobs.tasks.retention_sql`.
Provider cost lines and fetch runs keep their own 400 days (`app.reconciliation.retention`).

Evaluation data that describes traces goes with them, on the same cutoff: scores (a comment or a
judge's reasoning can quote the trace), online evaluator runs and review-queue items. Online runs
are kept until their month ends, because the evaluator's monthly budget sums them, and runs still
queued or running are left to the reaper. Datasets, experiments and their items are curated by
people and are kept."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql.elements import TextClause

from app.db.models import Project
from app.db.rls import bypass_rls
from app.evals.budget import month_start
from app.jobs.context import TaskContext
from app.jobs.tasks.retention_sql import (
    DELETE_ATTRIBUTION_ROLLUPS,
    DELETE_BATCH,
    DELETE_COST_ROLLUPS,
    DELETE_DETECTOR_RUNS,
    DELETE_ONLINE_EVALUATOR_RUNS,
    DELETE_QUEUE_ITEMS,
    DELETE_RESOLVED_INSIGHTS,
    DELETE_SCORES,
    DELETE_USER_STATS,
    DETECTOR_RUN_RETENTION,
)
from app.provider_status.retention import apply_provider_incident_retention
from app.reconciliation.retention import apply_billing_retention
from app.rollups.compute import floor_hour
from app.rollups.retention import delete_hourly_rollups

logger = structlog.get_logger(__name__)

BATCH_SIZE = 5000


async def run_retention(context: TaskContext, _: dict[str, Any]) -> None:
    now = datetime.now(UTC)
    deleted = await apply_retention(context.session_factory, now=now)
    insights, runs = await apply_insight_retention(context.session_factory, now=now)
    daily = await apply_daily_table_retention(context.session_factory, now=now)
    evals = await apply_eval_retention(context.session_factory, now=now)
    lines, fetch_runs = await apply_billing_retention(context.session_factory, now=now)
    incidents = await apply_provider_incident_retention(context.session_factory, now=now)
    logger.info(
        "retention_applied",
        traces_deleted=deleted,
        insights_deleted=insights,
        detector_runs_deleted=runs,
        user_stats_deleted=daily.user_stats,
        cost_rollups_deleted=daily.cost_rollups,
        attribution_rollups_deleted=daily.attribution_rollups,
        scores_deleted=evals.scores,
        evaluator_runs_deleted=evals.evaluator_runs,
        queue_items_deleted=evals.queue_items,
        provider_lines_deleted=lines,
        fetch_runs_deleted=fetch_runs,
        provider_incidents_deleted=incidents,
    )


async def apply_retention(
    session_factory: async_sessionmaker[AsyncSession], *, now: datetime
) -> int:
    async with session_factory() as db:
        projects = (await db.execute(select(Project.id, Project.retention_days))).all()

    total = 0
    for project_id, retention_days in projects:
        cutoff = now - timedelta(days=retention_days)
        while True:
            async with session_factory() as db:
                await bypass_rls(db)
                result = await db.execute(
                    DELETE_BATCH,
                    {"project_id": project_id, "cutoff": cutoff, "batch_size": BATCH_SIZE},
                )
                await db.commit()
            deleted = int(getattr(result, "rowcount", 0))
            total += deleted
            if deleted < BATCH_SIZE:
                break
        async with session_factory() as db:
            await bypass_rls(db)
            # Hourly rollups and telemetry losses: whole hours before the cutoff's hour.
            await delete_hourly_rollups(db, project_id, floor_hour(cutoff))
            await db.commit()
    return total


async def apply_insight_retention(
    session_factory: async_sessionmaker[AsyncSession], *, now: datetime
) -> tuple[int, int]:
    """Delete resolved insights past each project's retention (by `resolved_at`) and detector
    runs older than `DETECTOR_RUN_RETENTION`. Returns how many of each went."""
    async with session_factory() as db:
        projects = (await db.execute(select(Project.id, Project.retention_days))).all()

    insights = runs = 0
    for project_id, retention_days in projects:
        insights += await _delete_in_batches(
            session_factory,
            DELETE_RESOLVED_INSIGHTS,
            project_id=project_id,
            cutoff=now - timedelta(days=retention_days),
        )
        runs += await _delete_in_batches(
            session_factory,
            DELETE_DETECTOR_RUNS,
            project_id=project_id,
            cutoff=now - DETECTOR_RUN_RETENTION,
        )
    return insights, runs


@dataclass(slots=True)
class DailyRetention:
    user_stats: int = 0
    cost_rollups: int = 0
    attribution_rollups: int = 0


async def apply_daily_table_retention(
    session_factory: async_sessionmaker[AsyncSession], *, now: datetime
) -> DailyRetention:
    """Delete per-user daily stats, daily cost rollups and daily attribution rollups of days
    before each project's retention cutoff (UTC date). Returns how many of each went."""
    async with session_factory() as db:
        projects = (await db.execute(select(Project.id, Project.retention_days))).all()

    done = DailyRetention()
    for project_id, retention_days in projects:
        cutoff = now - timedelta(days=retention_days)
        done.user_stats += await _delete_in_batches(
            session_factory, DELETE_USER_STATS, project_id=project_id, cutoff=cutoff
        )
        done.cost_rollups += await _delete_in_batches(
            session_factory, DELETE_COST_ROLLUPS, project_id=project_id, cutoff=cutoff
        )
        done.attribution_rollups += await _delete_in_batches(
            session_factory, DELETE_ATTRIBUTION_ROLLUPS, project_id=project_id, cutoff=cutoff
        )
    return done


@dataclass(slots=True)
class EvalRetention:
    scores: int = 0
    evaluator_runs: int = 0
    queue_items: int = 0


async def apply_eval_retention(
    session_factory: async_sessionmaker[AsyncSession], *, now: datetime
) -> EvalRetention:
    """Delete scores, finished online evaluator runs and review-queue items older than each
    project's retention cutoff. Runs of the current month stay: the budget reads them."""
    async with session_factory() as db:
        projects = (await db.execute(select(Project.id, Project.retention_days))).all()

    done = EvalRetention()
    budget_month = month_start(now)
    for project_id, retention_days in projects:
        cutoff = now - timedelta(days=retention_days)
        done.scores += await _delete_in_batches(
            session_factory, DELETE_SCORES, project_id=project_id, cutoff=cutoff
        )
        done.evaluator_runs += await _delete_in_batches(
            session_factory,
            DELETE_ONLINE_EVALUATOR_RUNS,
            project_id=project_id,
            cutoff=min(cutoff, budget_month),
        )
        done.queue_items += await _delete_in_batches(
            session_factory, DELETE_QUEUE_ITEMS, project_id=project_id, cutoff=cutoff
        )
    return done


async def _delete_in_batches(
    session_factory: async_sessionmaker[AsyncSession],
    statement: TextClause,
    *,
    project_id: uuid.UUID,
    cutoff: datetime,
) -> int:
    """Run a batched DELETE until a batch comes back short, one transaction per batch."""
    total = 0
    params = {"project_id": project_id, "cutoff": cutoff, "batch_size": BATCH_SIZE}
    while True:
        async with session_factory() as db:
            await bypass_rls(db)
            result = await db.execute(statement, params)
            await db.commit()
        deleted = int(getattr(result, "rowcount", 0))
        total += deleted
        if deleted < BATCH_SIZE:
            return total
