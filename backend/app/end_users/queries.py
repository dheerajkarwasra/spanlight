"""Reads of the per-user daily stats and of a user's sessions.

The queries run in the caller's session and rely on its project binding for row-level security;
the explicit `project_id` filter is a second guard, not the only one.
"""

import uuid
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncSession

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
           max(day)          AS last_seen_day,
           (SELECT max(computed_at) FROM user_stats_daily
            WHERE project_id = :project_id AND day >= :first_day AND day < :end_day)
                             AS computed_at
    FROM user_stats_daily
    WHERE project_id = :project_id AND day >= :first_day AND day < :end_day
    GROUP BY external_user_id
    {having}
    ORDER BY {sort} DESC NULLS LAST, external_user_id
    LIMIT :limit
"""

# Rows after the cursor in (sort value descending, nulls last, user id ascending) order.
_AFTER_NUMBER = (
    "HAVING {sort} < CAST(:cursor_value AS numeric) OR {sort} IS NULL "
    "OR ({sort} = CAST(:cursor_value AS numeric) AND external_user_id > :cursor_user)"
)
_AFTER_NULL = "HAVING {sort} IS NULL AND external_user_id > :cursor_user"

_DAILY = text(
    """
    SELECT day, traces, llm_calls, errors, unpriced_calls, cost_usd, tokens,
           max(computed_at) OVER () AS computed_at
    FROM user_stats_daily
    WHERE project_id = :project_id AND external_user_id = :external_user_id
      AND day >= :first_day AND day < :end_day
    ORDER BY day
    """
)

# The same shape as the sessions list, for one user's traces.
_RECENT_SESSIONS = text(
    """
    SELECT session_id,
           count(*)           AS trace_count,
           min(started_at)    AS first_at,
           max(ended_at)      AS last_at,
           sum(cost_usd)      AS cost_usd,
           bool_or(has_unpriced) AS has_unpriced,
           sum(error_count)   AS error_count
    FROM traces
    WHERE project_id = :project_id AND external_user_id = :external_user_id
      AND session_id IS NOT NULL
    GROUP BY session_id
    ORDER BY max(ended_at) DESC, session_id DESC
    LIMIT :limit
    """
)


async def read_users(
    db: AsyncSession,
    project_id: uuid.UUID,
    first_day: date,
    end_day: date,
    sort: UserSort,
    limit: int,
    after: tuple[Decimal | None, str] | None,
) -> list[Row[Any]]:
    """Users summed over `[first_day, end_day)`, `sort` descending with unknowns last.

    Returns up to `limit` rows after the cursor `after = (sort value, user id)`, if any. Every
    row's `computed_at` is the latest recompute of any row of the window, whatever the page.
    """
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
        having = (_AFTER_NULL if value is None else _AFTER_NUMBER).format(sort=expression)
        params["cursor_user"] = user
        if value is not None:
            params["cursor_value"] = value
    sql = _USERS.format(cost=_COST, having=having, sort=expression)
    return list((await db.execute(text(sql), params)).all())


async def read_daily(
    db: AsyncSession, project_id: uuid.UUID, external_user_id: str, first_day: date, end_day: date
) -> list[Row[Any]]:
    """One user's stored days in `[first_day, end_day)`, oldest first. Every row's
    `computed_at` is the latest recompute of those days."""
    result = await db.execute(
        _DAILY,
        {
            "project_id": project_id,
            "external_user_id": external_user_id,
            "first_day": first_day,
            "end_day": end_day,
        },
    )
    return list(result.all())


async def read_recent_sessions(
    db: AsyncSession, project_id: uuid.UUID, external_user_id: str, limit: int
) -> list[Row[Any]]:
    """The user's sessions, the one with the latest activity first."""
    result = await db.execute(
        _RECENT_SESSIONS,
        {"project_id": project_id, "external_user_id": external_user_id, "limit": limit},
    )
    return list(result.all())
