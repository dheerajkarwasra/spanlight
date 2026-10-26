"""A budget's burn: its spend per UTC day of the current period, read like its evaluation.

The period is the one the budget's state shows (`app.budgets.periods`). Each day from the
period's start to `now` is one window of the `spend` metric with the budget's scope as the only
filter, all read by one `metric_series` call (one rollup read and one raw read); the first and
the current day can be partial. A day with spans and none priced is unknown (`None`); the running
total adds the known days only and stays unknown until the first of them.

A monthly budget also gets a forecast: the running total plus the mean spend of the last 7
complete UTC days (read in the same call; they may fall in the previous period) times the time
left in the period. Days that ended before the project was created are no history, not $0, so
they are left out; every later day counts, $0 included. The forecast is unknown under 3 days of
history or when one of them is unknown. A daily budget's horizon is hours, too short for a
daily rate, so it gets none.
"""

from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.metrics import metric_series
from app.alerts.rules import metric_filters
from app.alerts.types import Metric
from app.api.schemas.budgets import BudgetBurnOut, BurnPointOut
from app.api.window import TimeWindow
from app.budgets.periods import next_reset, period_start
from app.budgets.rule_sync import scope_filters
from app.budgets.types import BudgetPeriod
from app.db.models import Budget, Project

ONE_DAY = timedelta(days=1)
BASIS_DAYS = 7
MIN_BASIS_DAYS = 3
_FORECAST_QUANTUM = Decimal("0.00000001")


def day_windows(start: datetime, end: datetime) -> list[TimeWindow]:
    """One window per UTC day from `start` (a midnight) to `end`, the last one cut at `end`."""
    windows = []
    day = start
    while day < end:
        windows.append(TimeWindow(day, min(day + ONE_DAY, end)))
        day += ONE_DAY
    return windows


async def budget_burn(db: AsyncSession, budget: Budget, now: datetime) -> BudgetBurnOut:
    """The burn of `budget` up to `now`. The transaction must be bound to its project."""
    start = period_start(budget.period, now)
    resets_at = next_reset(budget.period, now)
    monthly = budget.period is BudgetPeriod.MONTHLY
    basis_start = period_start(BudgetPeriod.DAILY, now) - BASIS_DAYS * ONE_DAY
    windows = day_windows(min(start, basis_start) if monthly else start, min(now, resets_at))
    filters = metric_filters(scope_filters(budget.scope, budget.scope_id))
    values = await metric_series(db, budget.project_id, Metric.SPEND, windows, filters, now=now)
    spend_on = {
        window.start: measured.value for window, measured in zip(windows, values, strict=True)
    }
    points = []
    total: Decimal | None = None
    crosses_on: date | None = None
    for window in windows:
        if window.start < start:
            continue
        spend = spend_on[window.start]
        if spend is not None:
            total = spend if total is None else total + spend
        if crosses_on is None and total is not None and total > budget.amount_usd:
            crosses_on = window.start.date()
        points.append(BurnPointOut(day=window.start.date(), spend_usd=spend, cumulative_usd=total))
    basis: list[Decimal | None] = []
    if monthly:
        created = await db.scalar(select(Project.created_at).where(Project.id == budget.project_id))
        days = [basis_start + n * ONE_DAY for n in range(BASIS_DAYS)]
        basis = [spend_on[day] for day in days if created is not None and day + ONE_DAY > created]
    known = [spend for spend in basis if spend is not None]
    forecast: Decimal | None = None
    if total is not None and len(known) == len(basis) >= MIN_BASIS_DAYS:
        rate = sum(known, Decimal(0)) / len(known)
        remaining = Decimal((resets_at - now) / ONE_DAY)  # the rest of today included
        forecast = (total + rate * remaining).quantize(_FORECAST_QUANTUM, ROUND_HALF_UP)
        if crosses_on is None and rate > 0:
            reached = now + ONE_DAY * float((budget.amount_usd - total) / rate)
            crosses_on = reached.date() if reached < resets_at else None
    return BudgetBurnOut(
        period_start=start,
        resets_at=resets_at,
        amount_usd=budget.amount_usd,
        points=points,
        forecast_usd=forecast,
        forecast_basis_days=len(basis),
        forecast_crosses_on=crosses_on,
    )
