"""Reads of the per-user daily stats and of a user's sessions.

The queries run in the caller's session and rely on its project binding for row-level security;
the explicit `project_id` filter is a second guard, not the only one. Every read takes an
optional `environment`: given, only that environment's rows count (`""` names the empty
environment, as on `/traces`); `None` sums every environment, the traces without one included.
"""

import uuid
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.end_users.days import midnight
from app.explorer.trace_queries import escape_like
from app.explorer.trace_sort import SortOrder

UserSort = Literal["cost", "errors", "traces"]

# A user without a call has no cost to report; with calls and no priced one it is unknown.
_COST = "(CASE WHEN sum(llm_calls) = 0 THEN 0 ELSE sum(cost_usd) END)"
_SORT_EXPRESSIONS: dict[UserSort, str] = {
    "cost": _COST,
    "errors": "sum(errors)",
    "traces": "sum(traces)",
}

_USERS = """
    SELECT external_user_id,
           sum(traces)       AS traces,
           sum(llm_calls)    AS llm_calls,
           sum(errors)       AS errors,
           sum(unpriced_calls) AS unpriced_calls,
           {cost}            AS cost_usd,
           sum(tokens)       AS tokens,
           min(day)          AS first_seen_day,
           max(day)          AS last_seen_day
    FROM user_stats_daily
    WHERE project_id = :project_id AND day >= :first_day AND day < :end_day{environment}{search}
    GROUP BY external_user_id
    {having}
    ORDER BY {sort} {direction} NULLS LAST, external_user_id
    LIMIT :limit
"""

# Rows after the cursor in (sort value in `order`, nulls last, user id ascending) order;
# `{beyond}` is `<` for descending, `>` for ascending.
_AFTER_NUMBER = (
    "HAVING {sort} {beyond} CAST(:cursor_value AS numeric) OR {sort} IS NULL "
    "OR ({sort} = CAST(:cursor_value AS numeric) AND external_user_id > :cursor_user)"
)
_AFTER_NULL = "HAVING {sort} IS NULL AND external_user_id > :cursor_user"

# One row per day, the day's environments summed.
_DAILY = """
    SELECT day, sum(traces) AS traces, sum(llm_calls) AS llm_calls, sum(errors) AS errors,
           sum(unpriced_calls) AS unpriced_calls, sum(cost_usd) AS cost_usd,
           sum(tokens) AS tokens, max(max(computed_at)) OVER () AS computed_at
    FROM user_stats_daily
    WHERE project_id = :project_id AND external_user_id = :external_user_id
      AND day >= :first_day AND day < :end_day{environment}
    GROUP BY day
    ORDER BY day
"""

# The same shape as the sessions list, for one user's traces.
_RECENT_SESSIONS = """
    SELECT session_id,
           count(*)           AS trace_count,
           min(started_at)    AS first_at,
           max(ended_at)      AS last_at,
           sum(cost_usd)      AS cost_usd,
           bool_or(has_unpriced) AS has_unpriced,
           sum(error_count)   AS error_count
    FROM traces
    WHERE project_id = :project_id AND external_user_id = :external_user_id
      AND session_id IS NOT NULL{environment}
    GROUP BY session_id
    ORDER BY max(ended_at) DESC, session_id DESC
    LIMIT :limit
"""

PENDING_CAP = 10_000


def pending_sql(refreshed: str, trace_filter: str) -> str:
    """The window's latest recompute (`computed_at`) and how many of its traces are not counted
    yet (`pending_traces`, at most `:cap`): those started after the latest recompute of their own
    UTC day, and every trace of a day without rows.

    `refreshed` selects `day, computed_at` (the latest recompute per day) of the derived table;
    `trace_filter` narrows `traces AS t` past the project. Binds `:project_id`, `:first_day`,
    `:end_day`, `:start` (midnight of the first day) and `:cap`. Per day, because older days are
    recomputed less often than the last three; by trace start, the time the refreshes assign
    traces to days by. The lateral probe gives each day one `traces_project_started_idx` range,
    `(computed_at, next midnight)`, empty for a day recomputed after it ended (a plain join let
    the planner drop the day bounds and read the project from `-infinity`). The inner `LIMIT`
    keeps the planner from flattening the probe into such a join; the outer one stops the nested
    loop after `:cap` index entries in total, whatever the number of days.
    """
    return f"""
    WITH refreshed AS ({refreshed}),
    days AS (
        SELECT CAST(:first_day AS date) + i AS day,
               CAST(:start AS timestamptz) + i * interval '24 hours' AS start_at
        FROM generate_series(0, CAST(:end_day AS date) - CAST(:first_day AS date) - 1) AS i
    )
    SELECT (SELECT max(computed_at) FROM refreshed) AS computed_at,
           (SELECT count(*) FROM (
               SELECT 1
               FROM days
               LEFT JOIN refreshed ON refreshed.day = days.day
               CROSS JOIN LATERAL (
                   SELECT 1 FROM traces AS t
                   WHERE t.project_id = :project_id{trace_filter}
                     AND t.started_at > greatest(
                         refreshed.computed_at, days.start_at - interval '1 microsecond'
                     )
                     AND t.started_at < days.start_at + interval '24 hours'
                   LIMIT :cap
               ) AS hit
               LIMIT :cap
           ) AS capped) AS pending_traces
"""


# The latest recompute of each day of the window's user stats (a refresh writes every
# environment of a day), for `pending_sql`.
_REFRESHED = """
        SELECT day, max(computed_at) AS computed_at
        FROM user_stats_daily
        WHERE project_id = :project_id AND day >= :first_day AND day < :end_day
        GROUP BY day"""


def _environment_filter(environment: str | None, params: dict[str, Any]) -> str:
    """The `AND environment = ...` clause for `environment`, binding it into `params`."""
    if environment is None:
        return ""
    params["environment"] = environment
    return "\n      AND environment = :environment"


def _search_filter(q: str | None, params: dict[str, Any]) -> str:
    """The clause keeping user ids that contain `q` (case-insensitive, `%_\\` literal)."""
    if q is None:
        return ""
    params["q"] = f"%{escape_like(q)}%"
    return "\n      AND external_user_id ILIKE :q ESCAPE '\\'"


async def read_users(
    db: AsyncSession,
    project_id: uuid.UUID,
    first_day: date,
    end_day: date,
    sort: UserSort,
    limit: int,
    after: tuple[Decimal | None, str] | None,
    environment: str | None,
    q: str | None = None,
    order: SortOrder = "desc",
) -> list[Row[Any]]:
    """Up to `limit` users summed over `[first_day, end_day)` after the cursor `after = (sort
    value, user id)`, if any: `sort` in `order` with unknowns last, ties by user id ascending;
    `q` keeps the ids that contain it (see `_search_filter`)."""
    expression = _SORT_EXPRESSIONS[sort]
    params: dict[str, Any] = {
        "project_id": project_id,
        "first_day": first_day,
        "end_day": end_day,
        "limit": limit,
    }
    having = ""
    if after is not None:
        value, user = after
        having = (_AFTER_NULL if value is None else _AFTER_NUMBER).format(
            sort=expression, beyond="<" if order == "desc" else ">"
        )
        params["cursor_user"] = user
        if value is not None:
            params["cursor_value"] = value
    sql = _USERS.format(
        cost=_COST,
        having=having,
        sort=expression,
        direction={"asc": "ASC", "desc": "DESC"}[order],
        environment=_environment_filter(environment, params),
        search=_search_filter(q, params),
    )
    return list((await db.execute(text(sql), params)).all())


async def read_freshness(
    db: AsyncSession,
    project_id: uuid.UUID,
    first_day: date,
    end_day: date,
    environment: str | None,
) -> Row[Any]:
    """The window's `computed_at` and `pending_traces` (at most `PENDING_CAP`) of the user
    traces; see `pending_sql`."""
    params: dict[str, Any] = {
        "project_id": project_id,
        "first_day": first_day,
        "end_day": end_day,
        "start": midnight(first_day),
        "cap": PENDING_CAP,
    }
    environment_filter = _environment_filter(environment, params)
    trace_filter = "\n      AND external_user_id IS NOT NULL" + environment_filter
    return (await db.execute(text(pending_sql(_REFRESHED, trace_filter)), params)).one()


async def read_daily(
    db: AsyncSession,
    project_id: uuid.UUID,
    external_user_id: str,
    first_day: date,
    end_day: date,
    environment: str | None,
) -> list[Row[Any]]:
    """One user's stored days in `[first_day, end_day)`, oldest first. Every row's
    `computed_at` is the latest recompute of those days."""
    params: dict[str, Any] = {
        "project_id": project_id,
        "external_user_id": external_user_id,
        "first_day": first_day,
        "end_day": end_day,
    }
    sql = _DAILY.format(environment=_environment_filter(environment, params))
    return list((await db.execute(text(sql), params)).all())


async def read_recent_sessions(
    db: AsyncSession,
    project_id: uuid.UUID,
    external_user_id: str,
    limit: int,
    environment: str | None,
) -> list[Row[Any]]:
    """The user's sessions, the one with the latest activity first."""
    params: dict[str, Any] = {
        "project_id": project_id,
        "external_user_id": external_user_id,
        "limit": limit,
    }
    sql = _RECENT_SESSIONS.format(environment=_environment_filter(environment, params))
    return list((await db.execute(text(sql), params)).all())
