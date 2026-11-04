"""The `reap_budget_reservations` job, and the cleanup of old budget counters.

A reservation (`app.budgets.reserve`) whose call died before its span settled it would hold
the budget for ever. Every minute the reaper marks the rows past `expires_at` as `reaped` and
moves their amount from reserved to settled: the worst case, as ADR 0015 charges an abandoned
judge run. A late settle corrects a reaped row to the real cost and retains settled correlation.
Both tables are row-level secured and the sweep spans projects, so the job sets the worker bypass
flag. Rows are reaped in batches, one transaction each, so a row the database refuses stops one
batch, not every tenant's reaping, and no lock is held for long.

The same minute job then folds the pending `budget_bucket_deltas` of every budget into their
hour rows (`app.budgets.compaction`), so a budget no admission reads (an alert budget, a
disabled or idle block budget) does not accumulate a delta per ingest batch for ever.

`prune_budget_counters` deletes counter rows, and the deleted-span ledger (`budget_deleted_spans`)
by span start, after two monthly periods. Correlation stays while
its raw span exists and for at least 90 days from admission, since corrected timestamps can
still require its cross-period exclusion. Raw telemetry retention is unchanged.
"""

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import delete, exists, func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.budgets.compaction import compact_budget_deltas
from app.budgets.reserve import HELD, REAPED, RESERVATION_LEASE
from app.core.observability import (
    BUDGET_DELTAS_COMPACTED,
    BUDGET_RESERVATIONS_HELD,
    BUDGET_RESERVATIONS_REAPED,
)
from app.db.models import (
    BudgetBucketDelta,
    BudgetBucketSnapshot,
    BudgetBucketVersion,
    BudgetCounter,
    BudgetDeletedSpan,
    BudgetReservation,
    Span,
)
from app.db.rls import bypass_rls
from app.ingest.normalize import MAX_SPAN_AGE
from app.jobs.context import TaskContext

logger = structlog.get_logger(__name__)

REAP_JOB = "reap_budget_reservations"
BATCH_SIZE = 500
# Two monthly periods, the longest a budget has; a daily budget's rows go after the same time.
COUNTER_RETENTION = timedelta(days=62)
# Deltas one budget folds in one pass before the job warns that compaction is falling behind
# (a project writing 50 batches a second against an unread budget makes 180 000 an hour).
DELTA_BACKLOG_WARNING = 100_000


async def run_reap_budget_reservations(context: TaskContext, _: dict[str, Any]) -> None:
    reaped, held = await reap_expired(context.session_factory, now=datetime.now(UTC))
    BUDGET_RESERVATIONS_REAPED.inc(reaped)
    BUDGET_RESERVATIONS_HELD.set(held)
    logger.info("budget_reservations_reaped", reaped=reaped, held=held)
    compacted = await compact_pending_deltas(context.session_factory)
    BUDGET_DELTAS_COMPACTED.inc(compacted)


async def compact_pending_deltas(session_factory: async_sessionmaker[AsyncSession]) -> int:
    """Fold every budget's pending deltas into its hour rows, one transaction per budget, under
    the same row locks admission takes (so an admission waits for one budget's fold at most,
    and two budgets' rows are never held together in an order admission does not use). A
    budget deleted since the listing is skipped. Returns how many deltas were folded."""
    async with session_factory() as db:
        await bypass_rls(db)
        pending = (
            await db.execute(
                select(
                    BudgetBucketDelta.project_id,
                    BudgetBucketDelta.budget_id,
                    func.array_agg(func.distinct(BudgetBucketDelta.bucket_start)).label("hours"),
                )
                .group_by(BudgetBucketDelta.project_id, BudgetBucketDelta.budget_id)
                .order_by(BudgetBucketDelta.project_id, BudgetBucketDelta.budget_id)
            )
        ).all()
    folded = 0
    for row in pending:
        async with session_factory() as db:
            await bypass_rls(db)
            try:
                # Never recomputes: every hour is passed as missing (a row created here is
                # stale at -1 until an admission computes it) and none as stale.
                _, count = await compact_budget_deltas(
                    db, row.project_id, row.budget_id, row.hours, missing=row.hours
                )
                await db.commit()
            except IntegrityError:  # the budget (or project) was deleted since the listing
                await db.rollback()
                continue
        folded += count
        if count >= DELTA_BACKLOG_WARNING:
            logger.warning(
                "budget_deltas_backlog",
                project_id=str(row.project_id),
                budget_id=str(row.budget_id),
                folded=count,
            )
    return folded


async def reap_expired(
    session_factory: async_sessionmaker[AsyncSession], *, now: datetime
) -> tuple[int, int]:
    """Count every held reservation past `expires_at` at its estimate, `BATCH_SIZE` rows per
    transaction. Returns how many were reaped and how many are still held."""
    reaped = 0
    while True:
        async with session_factory() as db:
            await bypass_rls(db)
            reaped_now = await _reap_batch(db, now)
            held = await db.scalar(
                select(func.count())
                .select_from(BudgetReservation)
                .where(BudgetReservation.status == HELD)
            )
            await db.commit()
        reaped += reaped_now
        if reaped_now < BATCH_SIZE:
            return reaped, int(held or 0)


async def _reap_batch(db: AsyncSession, now: datetime) -> int:
    expired = (
        await db.execute(
            select(BudgetReservation.id, BudgetReservation.project_id)
            .where(BudgetReservation.status == HELD, BudgetReservation.expires_at <= now)
            .limit(BATCH_SIZE)
        )
    ).all()
    if not expired:
        return 0
    # The projects first, `KEY SHARE` in id order, as every ingest batch and settle does before
    # touching a reservation row: a budget edit's fence (`projects FOR UPDATE`) then drains
    # this batch before it cascades into or reclassifies the rows this batch holds, and the
    # trigger's version-row insert (a `KEY SHARE` on the project through its foreign key)
    # never waits on that fence while holding reservation rows the fence's holder wants.
    await db.execute(
        text(
            "SELECT id FROM projects WHERE id = ANY(CAST(:ids AS uuid[])) ORDER BY id FOR KEY SHARE"
        ),
        {"ids": sorted({row.project_id for row in expired})},
    )
    rows = (
        await db.execute(
            update(BudgetReservation)
            # Recheck after a competing reaper's row-lock wait before charging the row.
            .where(
                BudgetReservation.id.in_([row.id for row in expired]),
                BudgetReservation.status == HELD,
            )
            .values(status=REAPED)
            .returning(
                BudgetReservation.budget_id,
                BudgetReservation.period_start,
                BudgetReservation.amount_usd,
            )
            .execution_options(synchronize_session=False)
        )
    ).all()
    totals: dict[tuple[Any, datetime], Decimal] = defaultdict(Decimal)
    for row in rows:
        totals[(row.budget_id, row.period_start)] += Decimal(row.amount_usd)
    # In budget order, the order the guard locks counter rows in.
    for (budget_id, started), amount in sorted(totals.items()):
        await db.execute(
            update(BudgetCounter)
            .where(BudgetCounter.budget_id == budget_id, BudgetCounter.period_start == started)
            .values(
                reserved_usd=func.greatest(BudgetCounter.reserved_usd - amount, 0),
                settled_usd=BudgetCounter.settled_usd + amount,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
    return len(rows)


async def prune_budget_counters(
    session_factory: async_sessionmaker[AsyncSession], *, now: datetime
) -> int:
    """Prune old counters; retain identity while raw spans exist and for 90 days from admission.

    The latter is the ingest correction horizon, measured from the immutable admission clock,
    not the earlier month start. Raw retention is unchanged. Reintroducing a deleted natural
    identity after both retention horizons expire is outside the bounded deduplication contract.
    """
    cutoff = now - COUNTER_RETENTION
    async with session_factory() as db:
        await bypass_rls(db)
        reservations = await db.execute(
            delete(BudgetReservation)
            .where(
                BudgetReservation.expires_at < now - MAX_SPAN_AGE + RESERVATION_LEASE,
                ~exists().where(
                    Span.project_id == BudgetReservation.project_id,
                    Span.trace_id == BudgetReservation.trace_id,
                    Span.span_id == BudgetReservation.span_id,
                ),
            )
            .execution_options(synchronize_session=False)
        )
        counters = await db.execute(
            delete(BudgetCounter)
            .where(BudgetCounter.period_start < cutoff)
            .execution_options(synchronize_session=False)
        )
        deleted_spans = await db.execute(
            delete(BudgetDeletedSpan)
            .where(BudgetDeletedSpan.started_at < cutoff)
            .execution_options(synchronize_session=False)
        )
        buckets = [
            await db.execute(
                delete(table)
                .where(table.bucket_start < cutoff)
                .execution_options(synchronize_session=False)
            )
            for table in (BudgetBucketSnapshot, BudgetBucketDelta, BudgetBucketVersion)
        ]
        await db.commit()
    return sum(
        int(getattr(r, "rowcount", 0)) for r in (reservations, counters, deleted_spans, *buckets)
    )
