"""A budget's burn: its spend per UTC day of the current period, read like its evaluation.

The period is the one the budget's state shows (`app.budgets.periods`). Each day from the
period's start to `now` is one window of the `spend` metric with the budget's scope as the only
filter, all read by one `metric_series` call (one rollup read and one raw read); the first and
the current day can be partial. A day with spans and none priced is unknown (`None`); the running
total adds the known days only and stays unknown until the first of them.
"""

from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.metrics import metric_series
from app.alerts.rules import metric_filters
from app.alerts.types import Metric
from app.api.schemas.budgets import BudgetBurnOut, BurnPointOut
from app.api.window import TimeWindow
from app.budgets.periods import next_reset, period_start
from app.budgets.rule_sync import scope_filters
from app.db.models import Budget

ONE_DAY = timedelta(days=1)


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
    windows = day_windows(start, min(now, resets_at))
    filters = metric_filters(scope_filters(budget.scope, budget.scope_id))
    values = await metric_series(db, budget.project_id, Metric.SPEND, windows, filters, now=now)
    points = []
    total: Decimal | None = None
    for window, measured in zip(windows, values, strict=True):
        spend = measured.value
        if spend is not None:
            total = spend if total is None else total + spend
        points.append(BurnPointOut(day=window.start.date(), spend_usd=spend, cumulative_usd=total))
    return BudgetBurnOut(
        period_start=start, resets_at=resets_at, amount_usd=budget.amount_usd, points=points
    )
