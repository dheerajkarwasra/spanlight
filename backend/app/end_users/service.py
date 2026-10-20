"""End-user analytics use-cases: the users of a project and the detail of one.

The figures are the per-user daily stats the `refresh_user_stats` jobs keep (`refresh.py`), summed
over whole UTC days. A window is widened to whole days and the response says which; it spans at
most 90 days (the shared window limit), so a day series is at most 91 entries. `computed_at` is
the latest recompute of the rows summed, `None` when there are none.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Row
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.end_users import DayStats, StatsWindow, UserDetailOut, UserListOut, UserStats
from app.api.schemas.traces import SessionSummaryOut
from app.api.window import TimeWindow
from app.core.errors import not_found
from app.core.pagination import decode_value_cursor, encode_value_cursor
from app.core.wire import unescape_path_name
from app.end_users import queries
from app.end_users.days import DayTotals, UserTotals, day_bounds, fill_days, midnight, total
from app.end_users.queries import UserSort

RECENT_SESSIONS = 20


def _cost(value: Any) -> Decimal | None:
    return None if value is None else Decimal(value)


def _stats_window(first_day: date, end_day: date) -> StatsWindow:
    return StatsWindow(start=midnight(first_day), end=midnight(end_day))


def _user_stats(row: Row[Any]) -> UserStats:
    return UserStats(
        external_user_id=row.external_user_id,
        traces=int(row.traces),
        llm_calls=int(row.llm_calls),
        errors=int(row.errors),
        unpriced_calls=int(row.unpriced_calls),
        cost_usd=_cost(row.cost_usd),
        tokens=int(row.tokens),
        first_seen_day=row.first_seen_day,
        last_seen_day=row.last_seen_day,
    )


def _sort_value(item: UserStats, sort: UserSort) -> Decimal | int | None:
    if sort == "cost":
        return item.cost_usd
    return item.errors if sort == "errors" else item.traces


async def list_users(
    db: AsyncSession,
    project_id: uuid.UUID,
    window: TimeWindow,
    sort: UserSort,
    limit: int,
    cursor: str | None,
) -> UserListOut:
    """Users with activity in the window, `sort` descending with unknowns last (keyset)."""
    first_day, end_day = day_bounds(window)
    after = decode_value_cursor(cursor) if cursor else None
    rows = await queries.read_users(db, project_id, first_day, end_day, sort, limit + 1, after)
    items = [_user_stats(row) for row in rows[:limit]]
    next_cursor = None
    if len(rows) > limit:
        last = items[-1]
        next_cursor = encode_value_cursor(_sort_value(last, sort), last.external_user_id)
    return UserListOut(
        items=items,
        next_cursor=next_cursor,
        window=_stats_window(first_day, end_day),
        computed_at=_computed_at(rows),
    )


def _computed_at(rows: list[Row[Any]]) -> datetime | None:
    """The latest recompute the queries report on every row, `None` without rows."""
    return rows[0].computed_at if rows else None


def _stored_day(row: Row[Any]) -> DayTotals:
    llm_calls = int(row.llm_calls)
    cost = _cost(row.cost_usd)
    return DayTotals(
        day=row.day,
        traces=int(row.traces),
        llm_calls=llm_calls,
        errors=int(row.errors),
        unpriced_calls=int(row.unpriced_calls),
        # No call, nothing to price: that day cost nothing rather than an unknown amount.
        cost_usd=Decimal(0) if cost is None and llm_calls == 0 else cost,
        tokens=int(row.tokens),
    )


def _day_stats(day: DayTotals) -> DayStats:
    return DayStats(
        day=day.day,
        traces=day.traces,
        llm_calls=day.llm_calls,
        errors=day.errors,
        unpriced_calls=day.unpriced_calls,
        cost_usd=day.cost_usd,
        tokens=day.tokens,
    )


def _totals_stats(external_user_id: str, totals: UserTotals) -> UserStats:
    return UserStats(
        external_user_id=external_user_id,
        traces=totals.traces,
        llm_calls=totals.llm_calls,
        errors=totals.errors,
        unpriced_calls=totals.unpriced_calls,
        cost_usd=totals.cost_usd,
        tokens=totals.tokens,
        first_seen_day=totals.first_seen_day,
        last_seen_day=totals.last_seen_day,
    )


def _session(row: Row[Any]) -> SessionSummaryOut:
    return SessionSummaryOut(
        session_id=row.session_id,
        trace_count=int(row.trace_count),
        first_at=row.first_at,
        last_at=row.last_at,
        cost_usd=_cost(row.cost_usd),
        has_unpriced=bool(row.has_unpriced),
        error_count=int(row.error_count),
    )


def unescape_user_id(wire_id: str) -> str:
    """The end user id a detail URL names (see `app.core.wire.unescape_path_name`)."""
    return unescape_path_name(wire_id)


async def user_detail(
    db: AsyncSession, project_id: uuid.UUID, wire_id: str, window: TimeWindow
) -> UserDetailOut:
    """The user's totals, a gapless day series and their latest sessions. `wire_id` is the id
    as the URL carries it (see `unescape_user_id`). 404 when the user has no activity in the
    window."""
    external_user_id = unescape_user_id(wire_id)
    if not external_user_id:
        raise not_found("No end user with this id has activity in the window.")
    first_day, end_day = day_bounds(window)
    rows = await queries.read_daily(db, project_id, external_user_id, first_day, end_day)
    stored = [_stored_day(row) for row in rows]
    totals = total(stored)
    if totals is None:
        raise not_found("No end user with this id has activity in the window.")
    sessions = await queries.read_recent_sessions(db, project_id, external_user_id, RECENT_SESSIONS)
    return UserDetailOut(
        user=_totals_stats(external_user_id, totals),
        daily=[_day_stats(day) for day in fill_days(first_day, end_day, stored)],
        recent_sessions=[_session(row) for row in sessions],
        window=_stats_window(first_day, end_day),
        computed_at=_computed_at(rows),
    )
