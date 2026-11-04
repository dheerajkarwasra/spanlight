"""Retention: one bounded batch per opportunity, rotating over cleanup classes and their scopes.

Traces (and, by cascade, their spans) go past each project's retention, with the project's hourly
rollups and telemetry losses (`app.rollups.retention`), resolved insights, old detector runs, old
per-user daily stats, daily cost rollups and daily attribution rollups; the statements are in
`app.jobs.tasks.retention_sql`. Evaluation data that describes traces goes on the same cutoff:
scores (a comment or a judge's reasoning can quote the trace), online evaluator runs (kept until
their month ends, because the evaluator's monthly budget sums them; runs still queued or running
are left to the reaper) and review-queue items. Datasets, experiments and their items are curated
by people and are kept. Provider cost lines and fetch runs keep their own 400 days
(`app.reconciliation.retention`), provider incidents 730 (`app.provider_status.retention`), audit
events `AUDIT_RETENTION_DAYS` (0 keeps them all).

Each hourly run serves up to `MAX_OPPORTUNITIES` opportunities. An opportunity is one class and
one scope, chosen by the durable cursor in `app.jobs.tasks.retention_cursor`, and one work
transaction: lock the project (`lock_aggregate_sources` where derived data or source-loss evidence
is involved), re-read its retention, run one bounded statement, check the job's lease is still
live, commit. Trace deletion keeps parent and cascade atomic: `TRACE_BATCH` parents, all their
spans, and the source-loss markers of every hour they touch, in one transaction. A batch that hits
its lock or statement timeout rolls back and the scope waits for its next turn; nothing drains.
Every class gets an opportunity every run, and every (class, project) pair is revisited within
ceil(projects / (MAX_OPPORTUNITIES / 14)) runs when the cursor rows are not contended.
"""

import asyncio
import uuid
from collections import Counter
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

import structlog
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import TextClause

from app.budgets.buckets import bump_hours_of_traces
from app.core.db_errors import is_statement_timeout, sqlstate
from app.core.observability import RETENTION_BATCHES
from app.db.rls import bypass_rls
from app.evals.budget import month_start
from app.jobs import aggregate_progress as progress
from app.jobs.context import TaskContext
from app.jobs.fencing import lock_live_job
from app.jobs.queue import LeaseLostError
from app.jobs.tasks.retention_cursor import (
    CLASSES,
    SOURCE_CLASSES,
    Opportunity,
    next_opportunity,
    require_classes,
)
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
    SELECT_BATCH,
    SELECT_POLICY,
)
from app.provider_status.retention import delete_old_incidents
from app.reconciliation.retention import delete_billing_batch
from app.rollups.compute import floor_hour
from app.rollups.coverage import lock_aggregate_sources, mark_trace_source_loss
from app.rollups.retention import delete_hourly_rollups
from app.services.audit import purge_audit_events

logger = structlog.get_logger(__name__)

# Measured locally on PostgreSQL 17 (docs/runbooks/retention.md): 1 000 parents with four 512-byte
# spans each delete in 0.05 s, 5 000 in 0.4 s, one parent with 10 000 spans in 0.03 s; an empty
# opportunity costs about 3 ms. Bounds, not a completion promise: a backlog drains one batch per
# visit, so a project is revisited within ceil(projects / 32) runs.
TRACE_BATCH = 1000
ROW_BATCH = 5000
# Opportunities per run: 32 rounds over the 14 classes when no cursor row is contended.
MAX_OPPORTUNITIES = 448
# Per-opportunity work transaction; the run's 30 s start / 40 s outer budget comes from
# `aggregate_progress`, inside the worker's 50 s short-job allowance.
TRANSACTION_SECONDS = 10.0


def _policy_cutoff(now: datetime, retention_days: int) -> datetime:
    return now - timedelta(days=retention_days)


# The project classes that are one bounded DELETE: statement, cutoff from the project's policy,
# and whether the derived table shares the aggregate source lock.
_ROW_CLASSES: dict[str, tuple[TextClause, Callable[[datetime, int], datetime], bool]] = {
    "resolved_insights": (DELETE_RESOLVED_INSIGHTS, _policy_cutoff, False),
    "detector_runs": (DELETE_DETECTOR_RUNS, lambda now, _: now - DETECTOR_RUN_RETENTION, False),
    "user_stats": (DELETE_USER_STATS, _policy_cutoff, True),
    "daily_cost": (DELETE_COST_ROLLUPS, _policy_cutoff, True),
    "daily_attribution": (DELETE_ATTRIBUTION_ROLLUPS, _policy_cutoff, True),
    "scores": (DELETE_SCORES, _policy_cutoff, False),
    "online_evaluator_runs": (
        DELETE_ONLINE_EVALUATOR_RUNS,
        lambda now, days: min(_policy_cutoff(now, days), month_start(now)),
        False,
    ),
    "annotation_queue_items": (DELETE_QUEUE_ITEMS, _policy_cutoff, False),
}


async def run_retention(context: TaskContext, _: dict[str, Any]) -> None:
    now = context.clock()
    started = asyncio.get_running_loop().time()
    deadline = started + progress.OUTER_SECONDS
    start_deadline = started + progress.START_SECONDS
    async with context.session_factory() as db:
        await require_classes(db)

    outcomes: Counter[str] = Counter()
    deleted: Counter[str] = Counter()
    opportunities = 0
    # Work this run saw but did not reach: a full batch, a failed batch, or an early stop.
    unreached = False
    while opportunities < MAX_OPPORTUNITIES and progress.remaining(start_deadline):
        try:
            # One heartbeat per round of the 14 classes: the run ends inside the 60 s lease anyway.
            if opportunities % len(CLASSES) == 0:
                await progress.heartbeat(context, deadline)
            opportunity = await next_opportunity(context, deadline)
        except TimeoutError:
            outcomes["discovery_timeout"] += 1
            unreached = True
            break
        except DBAPIError as error:
            logger.warning("retention_discovery_failed", sqlstate=sqlstate(error))
            outcomes["discovery_error"] += 1
            unreached = True
            break
        if opportunity is None:
            outcomes["contended"] += 1
            unreached = True
            break
        opportunities += 1
        outcome, rows = await _serve(context, opportunity, now, deadline)
        RETENTION_BATCHES.labels(opportunity.cleanup_class, outcome).inc()
        outcomes[outcome] += 1
        deleted[opportunity.cleanup_class] += rows
        cap = TRACE_BATCH if opportunity.cleanup_class == "trace_cascades" else ROW_BATCH
        if outcome not in ("deleted", "empty", "no_scope") or rows >= cap:
            unreached = True
    logger.info(
        "retention_applied",
        opportunities=opportunities,
        outcomes=dict(outcomes),
        deleted=dict(deleted),
        incomplete=unreached or opportunities < MAX_OPPORTUNITIES,
        elapsed_s=round(asyncio.get_running_loop().time() - started, 3),
    )


async def _serve(
    context: TaskContext, opportunity: Opportunity, now: datetime, deadline: float
) -> tuple[str, int]:
    """One work transaction for the opportunity: (outcome, rows deleted). Lease loss propagates
    and ends the run; any other failure rolls this batch back and the run goes on."""
    seconds = min(TRANSACTION_SECONDS, progress.remaining(deadline))
    try:
        async with asyncio.timeout(seconds), context.session_factory() as db:
            try:
                await progress.set_limits(db, seconds)
                rows = await delete_batch(
                    db,
                    opportunity.cleanup_class,
                    opportunity.scope_id,
                    now,
                    audit_days=context.settings.audit_retention_days,
                )
                if rows is None:
                    return "no_scope", 0
                await lock_live_job(db, context.job)
                await db.commit()
                return ("deleted" if rows else "empty"), rows
            except BaseException:
                await db.invalidate()
                raise
    except LeaseLostError:
        raise
    except TimeoutError:
        return "timeout", 0
    except DBAPIError as error:
        if sqlstate(error) == "55P03":
            return "lock_timeout", 0
        if is_statement_timeout(error.orig):
            return "statement_timeout", 0
        logger.warning(
            "retention_batch_failed",
            cleanup_class=opportunity.cleanup_class,
            scope_id=str(opportunity.scope_id),
            sqlstate=sqlstate(error),
        )
        return "error", 0
    except Exception:
        logger.exception(
            "retention_batch_failed",
            cleanup_class=opportunity.cleanup_class,
            scope_id=str(opportunity.scope_id),
        )
        return "error", 0


async def delete_batch(
    db: AsyncSession,
    cleanup_class: str,
    scope_id: uuid.UUID | None,
    now: datetime,
    *,
    audit_days: int,
) -> int | None:
    """One bounded batch of `cleanup_class` for `scope_id`; the caller commits.

    Returns the rows deleted (parents, for traces), or None when the scope is gone or the roster
    is empty. Project classes set the worker bypass and lock the project before reading its
    current retention, so a policy extension committed before that read is honored.
    """
    if cleanup_class == "provider_incidents":
        return await delete_old_incidents(db, now, ROW_BATCH)
    if cleanup_class == "audit_events":
        if audit_days == 0:
            return 0
        before = now - timedelta(days=audit_days)
        return await purge_audit_events(db, before=before, batch_size=ROW_BATCH)
    if scope_id is None:
        return None
    if cleanup_class in SOURCE_CLASSES:
        return await delete_billing_batch(db, cleanup_class, scope_id, now, ROW_BATCH)
    await bypass_rls(db)
    if cleanup_class == "trace_cascades":
        return await _delete_trace_batch(db, scope_id, now)
    if cleanup_class == "hourly_aggregates":
        retention_days = await _lock_policy(db, scope_id, source_lock=True)
        if retention_days is None:
            return None
        cutoff_hour = floor_hour(_policy_cutoff(now, retention_days))
        return await delete_hourly_rollups(db, scope_id, cutoff_hour, ROW_BATCH)
    statement, cutoff, source_lock = _ROW_CLASSES[cleanup_class]
    retention_days = await _lock_policy(db, scope_id, source_lock=source_lock)
    if retention_days is None:
        return None
    result = await db.execute(
        statement,
        {"project_id": scope_id, "cutoff": cutoff(now, retention_days), "batch_size": ROW_BATCH},
    )
    return int(getattr(result, "rowcount", 0))


async def _lock_policy(db: AsyncSession, project_id: uuid.UUID, *, source_lock: bool) -> int | None:
    """The project's current `retention_days` under its KEY SHARE lock (and the aggregate source
    lock when asked), or None when the project is gone."""
    if source_lock and not await lock_aggregate_sources(db, project_id):
        return None
    days = await db.scalar(SELECT_POLICY, {"project_id": project_id})
    return None if days is None else int(days)


async def _delete_trace_batch(db: AsyncSession, project_id: uuid.UUID, now: datetime) -> int | None:
    """Up to `TRACE_BATCH` expired traces with their spans, after marking every hour they touch
    as lost to the aggregates and taking the budget rows their deletion bumps."""
    retention_days = await _lock_policy(db, project_id, source_lock=True)
    if retention_days is None:
        return None
    params = {
        "project_id": project_id,
        "cutoff": _policy_cutoff(now, retention_days),
        "batch_size": TRACE_BATCH,
    }
    trace_ids = list(await db.scalars(SELECT_BATCH, params))
    await mark_trace_source_loss(db, project_id, trace_ids, "retention")
    await bump_hours_of_traces(db, project_id, trace_ids)
    result = await db.execute(DELETE_BATCH, {"project_id": project_id, "trace_ids": trace_ids})
    return int(getattr(result, "rowcount", 0))
