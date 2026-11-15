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
from app.end_users.refresh import user_aggregate_sql
from app.end_users.tail_sql import TAIL_FILTER, pending_sql
from app.explorer.trace_queries import escape_like
from app.explorer.trace_sort import SortOrder
from app.rollups.coverage_reads import CoverageState, read_covered
from app.rollups.dirty import USER_DAILY
from app.rollups.watermark import RAW_TAIL_MAX

UserSort = Literal["cost", "errors", "traces"]

# A user without a call has no cost to report; with calls and no priced one it is unknown.
_COST = "(CASE WHEN sum(llm_calls) = 0 THEN 0 ELSE sum(cost_usd) END)"
# Tokens follow the same rule: a day without calls stores NULL tokens.
_TOKENS = "(CASE WHEN sum(llm_calls) = 0 THEN 0 ELSE sum(tokens) END)"
_SORT_EXPRESSIONS: dict[UserSort, str] = {
    "cost": _COST,
    "errors": "sum(errors)",
    "traces": "sum(traces)",
}

# The stored days of the window and the raw tail, merged per user in one statement so the sort,
# the keyset and the page see the sum. The tail is the traces after their own day's latest
# recompute and after `now() - :tail_max`, aggregated as the refresh does (`user_aggregate_sql`);
# older uncomputed traces stay in `pending_traces`. `aggregate_coverage` is the CTE `read_covered`
# wraps every statement in: without complete coverage the raw tail is left out, as before it
# existed. `refreshed` is materialized so the per-trace probe reads it, not the table. The
# per-day bound is correlated, so it only filters; the uncorrelated one before it (the earliest
# tail trace, found by one index probe per tail day) starts the index range there.
_USERS = """
    WITH refreshed AS MATERIALIZED ({refreshed}),
    tail AS ({tail})
    SELECT external_user_id,
           sum(traces)       AS traces,
           sum(llm_calls)    AS llm_calls,
           sum(errors)       AS errors,
           sum(unpriced_calls) AS unpriced_calls,
           {cost}            AS cost_usd,
           {tokens}          AS tokens,
           min(day)          AS first_seen_day,
           max(day)          AS last_seen_day
    FROM (
        SELECT day, environment, external_user_id, traces, llm_calls, errors, unpriced_calls,
               cost_usd, tokens
        FROM user_stats_daily
        WHERE project_id = :project_id AND day >= :first_day AND day < :end_day
        UNION ALL
        SELECT * FROM tail
    ) AS merged
    WHERE true{environment}{search}
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

# One row per day, the day's environments summed, stored days and raw tail merged as in `_USERS`.
_DAILY = """
    WITH refreshed AS MATERIALIZED ({refreshed}),
    tail AS ({tail})
    SELECT day, sum(traces) AS traces, sum(llm_calls) AS llm_calls, sum(errors) AS errors,
           sum(unpriced_calls) AS unpriced_calls, sum(cost_usd) AS cost_usd,
           sum(tokens) AS tokens, max(max(computed_at)) OVER () AS computed_at
    FROM (
        SELECT day, environment, external_user_id, traces, llm_calls, errors, unpriced_calls,
               cost_usd, tokens, computed_at
        FROM user_stats_daily
        WHERE project_id = :project_id AND external_user_id = :external_user_id
          AND day >= :first_day AND day < :end_day
        UNION ALL
        SELECT *, CAST(NULL AS timestamptz) FROM tail
    ) AS merged
    WHERE true{environment}
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
    coverage: CoverageState | None = None,
) -> list[Row[Any]]:
    """Up to `limit` users summed over `[first_day, end_day)` after the cursor `after = (sort
    value, user id)`, if any: `sort` in `order` with unknowns last, ties by user id ascending;
    `q` keeps the ids that contain it (see `_search_filter`). The stored days and the raw tail
    are summed together (see `_USERS`)."""
    expression = _SORT_EXPRESSIONS[sort]
    params: dict[str, Any] = {
        "project_id": project_id,
        "first_day": first_day,
        "end_day": end_day,
        "start": midnight(first_day),
        "end": midnight(end_day),
        "tail_max": RAW_TAIL_MAX,
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
        refreshed=_REFRESHED,
        tail=user_aggregate_sql(TAIL_FILTER),
        cost=_COST,
        tokens=_TOKENS,
        having=having,
        sort=expression,
        direction={"asc": "ASC", "desc": "DESC"}[order],
        environment=_environment_filter(environment, params),
        search=_search_filter(q, params),
    )
    return await read_covered(
        db,
        text(sql),
        params,
        project_id,
        midnight(first_day),
        midnight(end_day),
        coverage,
        relevant_consumers=USER_DAILY,
    )


async def read_freshness(
    db: AsyncSession,
    project_id: uuid.UUID,
    first_day: date,
    end_day: date,
    environment: str | None,
) -> Row[Any]:
    """The window's `computed_at` and `pending_traces` (at most `PENDING_CAP`) of the user
    traces; see `pending_sql`. The traces of the raw tail (see `_USERS`) are counted by
    `read_users`, so only the older ones are pending."""
    params: dict[str, Any] = {
        "project_id": project_id,
        "first_day": first_day,
        "end_day": end_day,
        "start": midnight(first_day),
        "cap": PENDING_CAP,
        "tail_max": RAW_TAIL_MAX,
    }
    environment_filter = _environment_filter(environment, params)
    trace_filter = (
        "\n      AND external_user_id IS NOT NULL"
        "\n      AND t.started_at <= now() - CAST(:tail_max AS interval)" + environment_filter
    )
    return (await db.execute(text(pending_sql(_REFRESHED, trace_filter)), params)).one()


async def read_daily(
    db: AsyncSession,
    project_id: uuid.UUID,
    external_user_id: str,
    first_day: date,
    end_day: date,
    environment: str | None,
    coverage: CoverageState | None = None,
) -> list[Row[Any]]:
    """One user's days in `[first_day, end_day)`, oldest first, the raw tail included (see
    `_USERS`). Every row's `computed_at` is the latest recompute of the stored days, NULL when
    every day comes from the tail."""
    params: dict[str, Any] = {
        "project_id": project_id,
        "external_user_id": external_user_id,
        "first_day": first_day,
        "end_day": end_day,
        "start": midnight(first_day),
        "end": midnight(end_day),
        "tail_max": RAW_TAIL_MAX,
    }
    sql = _DAILY.format(
        refreshed=_REFRESHED,
        tail=user_aggregate_sql(
            "\n          AND t.external_user_id = :external_user_id" + TAIL_FILTER
        ),
        environment=_environment_filter(environment, params),
    )
    return await read_covered(
        db,
        text(sql),
        params,
        project_id,
        midnight(first_day),
        midnight(end_day),
        coverage,
        relevant_consumers=USER_DAILY,
    )


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
