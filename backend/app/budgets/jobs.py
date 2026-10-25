"""The `reap_budget_reservations` job, and the cleanup of old budget counters.

A reservation (`app.budgets.reserve`) whose call died before its span settled it would hold
the budget for ever. Every minute the reaper marks the rows past `expires_at` as `reaped` and
moves their amount from reserved to settled: the worst case, as ADR 0015 charges an abandoned
judge run. A late settle corrects a reaped row to the real cost and deletes it. Both tables are
row-level secured and the sweep spans projects, so the job sets the worker bypass flag. Rows
are reaped in batches, one transaction each, so a row the database refuses stops one batch,
not every tenant's reaping, and no lock is held for long.

`prune_budget_counters` (the hourly cleanup job) deletes counters and reaped reservations older
than two monthly periods: nothing reads them any more.
"""

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.budgets.reserve import HELD, REAPED
from app.core.observability import BUDGET_RESERVATIONS_HELD, BUDGET_RESERVATIONS_REAPED
from app.db.models import BudgetCounter, BudgetReservation
from app.db.rls import bypass_rls
from app.jobs.context import TaskContext

logger = structlog.get_logger(__name__)

REAP_JOB = "reap_budget_reservations"
BATCH_SIZE = 500
# Two monthly periods, the longest a budget has; a daily budget's rows go after the same time.
COUNTER_RETENTION = timedelta(days=62)


async def run_reap_budget_reservations(context: TaskContext, _: dict[str, Any]) -> None:
    reaped, held = await reap_expired(context.session_factory, now=datetime.now(UTC))
    BUDGET_RESERVATIONS_REAPED.inc(reaped)
    BUDGET_RESERVATIONS_HELD.set(held)
    logger.info("budget_reservations_reaped", reaped=reaped, held=held)


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
        select(BudgetReservation.id)
        .where(BudgetReservation.status == HELD, BudgetReservation.expires_at <= now)
        .limit(BATCH_SIZE)
    )
    rows = (
        await db.execute(
            update(BudgetReservation)
            .where(BudgetReservation.id.in_(expired))
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
    """Delete counters and reservations of periods that began over `COUNTER_RETENTION` ago."""
    cutoff = now - COUNTER_RETENTION
    async with session_factory() as db:
        await bypass_rls(db)
        reservations = await db.execute(
            delete(BudgetReservation)
            .where(BudgetReservation.period_start < cutoff)
            .execution_options(synchronize_session=False)
        )
        counters = await db.execute(
            delete(BudgetCounter)
            .where(BudgetCounter.period_start < cutoff)
            .execution_options(synchronize_session=False)
        )
        await db.commit()
    return int(getattr(reservations, "rowcount", 0)) + int(getattr(counters, "rowcount", 0))
