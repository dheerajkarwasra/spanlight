"""Whole UTC days: the window of a read, the days a refresh recomputes and gapless day series.

Pure functions, no I/O. The stats are stored per UTC day, so a request's `from`/`to` are widened
to whole days (`from` floored, `to` ceiled) and the response reports the widened window.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

from app.api.window import TimeWindow

ONE_DAY = timedelta(days=1)
# How many days, today included, the 15-minute refresh and the nightly one recompute.
RECENT_DAYS = 3
DEEP_DAYS = 8


@dataclass(frozen=True, slots=True)
class DayTotals:
    """One user's figures for one day. `cost_usd` is `None` only when calls were made and none
    was priced; a day without calls cost nothing."""

    day: date
    traces: int
    llm_calls: int
    errors: int
    unpriced_calls: int
    cost_usd: Decimal | None
    tokens: int


@dataclass(frozen=True, slots=True)
class UserTotals:
    """One user's figures summed over the days that have any, with the first and last of them."""

    traces: int
    llm_calls: int
    errors: int
    unpriced_calls: int
    cost_usd: Decimal | None
    tokens: int
    first_seen_day: date
    last_seen_day: date


def day_bounds(window: TimeWindow) -> tuple[date, date]:
    """The first day of the window and the day after its last, whole UTC days."""
    start = window.start.astimezone(UTC)
    end = window.end.astimezone(UTC)
    first = start.date()
    last = end.date() if end.time() == time(0) else _next_day(end.date())
    return first, last


def midnight(day: date) -> datetime:
    return datetime.combine(day, time(0), tzinfo=UTC)


def recent_days(now: datetime) -> list[date]:
    """The day before yesterday, yesterday and today (UTC): the days every refresh recomputes."""
    return _last_days(now, RECENT_DAYS)


def deep_days(now: datetime) -> list[date]:
    """The last eight days (UTC), today included: the days the nightly refresh recomputes."""
    return _last_days(now, DEEP_DAYS)


def _last_days(now: datetime, count: int) -> list[date]:
    today = now.astimezone(UTC).date()
    return [today - offset * ONE_DAY for offset in range(count - 1, -1, -1)]


def fill_days(first: date, end: date, rows: Iterable[DayTotals]) -> list[DayTotals]:
    """One entry per day in `[first, end)`: the stored row, or zeros (cost 0) for a day without."""
    stored = {row.day: row for row in rows}
    days: list[DayTotals] = []
    day = first
    while day < end:
        days.append(stored.get(day) or DayTotals(day, 0, 0, 0, 0, Decimal(0), 0))
        day = _next_day(day)
    return days


def total(rows: Sequence[DayTotals]) -> UserTotals | None:
    """The rows summed, or `None` when there are none. Rows with no traces are ignored.

    The cost is the sum of the days that have one: `None` when calls were made and none was ever
    priced, a lower bound when only some were.
    """
    seen = [row for row in rows if row.traces > 0]
    if not seen:
        return None
    priced = [row.cost_usd for row in seen if row.cost_usd is not None]
    calls = sum(row.llm_calls for row in seen)
    cost = Decimal(sum(priced)) if priced else (Decimal(0) if calls == 0 else None)
    return UserTotals(
        traces=sum(row.traces for row in seen),
        llm_calls=calls,
        errors=sum(row.errors for row in seen),
        unpriced_calls=sum(row.unpriced_calls for row in seen),
        cost_usd=cost,
        tokens=sum(row.tokens for row in seen),
        first_seen_day=min(row.day for row in seen),
        last_seen_day=max(row.day for row in seen),
    )


def _next_day(day: date) -> date:
    # The calendar ends at 9999-12-31; a window ending there has no day after it.
    return date.max if day == date.max else day + ONE_DAY
