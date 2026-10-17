"""Reads behind the attribution endpoint: a page of groups and the window's buckets and totals.

Two sources, one shape. The raw source is the `grouped` CTE of `aggregate_sql` over the traces
of the window (one row per environment and group); the rollup source is `attribution_rollups_daily`
over whole UTC days (one row per day, environment and bucket). Both are summed per group with
the same expressions, so the two paths agree by construction, and the rollup read sums across
days and environments exactly as the raw read sums across environments.

Every query runs in the caller's session, bound to the project for row-level security; the
explicit `project_id` filter is the second guard. The queries only read.
"""

import uuid
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.window import TimeWindow
from app.attribution.aggregate_sql import GroupKind, grouped_cte, money_sum, retry_cost_sum

AttributionSort = Literal["cost", "calls", "errors", "retry_cost"]

# The figures of a set of rows `r`, summed. Counts add; `cost_usd` is unknown from $1M up as
# everywhere; `retries` ignores rows without one and reads NULL when none has one; the retry
# cost is unknown as soon as a row has retries it could not price (`retry_cost_sum`).
_SUMS = f"""
           sum(r.traces)::bigint AS traces,
           sum(r.llm_calls)::bigint AS llm_calls,
           sum(r.errors)::bigint AS errors,
           sum(r.unpriced_calls)::bigint AS unpriced_calls,
           sum(r.input_tokens)::bigint AS input_tokens,
           sum(r.output_tokens)::bigint AS output_tokens,
           {money_sum("r.cost_usd")} AS cost_usd,
           sum(r.retries)::bigint AS retries,
           {retry_cost_sum("r.retries", "r.retry_cost_usd")} AS retry_cost_usd"""

_SORT_EXPRESSIONS: dict[AttributionSort, str] = {
    "cost": money_sum("r.cost_usd"),
    "calls": "sum(r.llm_calls)",
    "errors": "sum(r.errors)",
    "retry_cost": retry_cost_sum("r.retries", "r.retry_cost_usd"),
}

# The raw source, in the shape of the rollup table: the `none` bucket is the group without a
# value, and the raw path never folds, so there is no `other` row.
_RAW_SOURCE = """
    (SELECT CASE WHEN grouped.group_value IS NULL THEN 'none' ELSE 'value' END AS bucket,
            CAST(NULL AS integer) AS folded_values,
            grouped.*
     FROM grouped) AS r"""

_ROLLUP_SOURCE = """
    attribution_rollups_daily AS r
    WHERE r.project_id = :project_id
      AND r.group_kind = CAST(:kind AS text)
      AND {key}
      AND r.day >= :first_day
      AND r.day < :end_day{environment}"""

# One row per value, after the cursor, in (sort value descending, unknowns last, value
# ascending) order: the keyset shape of the Users list.
_ITEMS = """
    SELECT r.group_value AS value,{sums}
    FROM {source}
    {where}r.bucket = 'value'
    GROUP BY r.group_value
    {having}
    ORDER BY {sort} DESC NULLS LAST, r.group_value ASC
    LIMIT :limit
"""
_AFTER_NUMBER = (
    "HAVING {sort} < CAST(:cursor_value AS numeric) OR {sort} IS NULL "
    "OR ({sort} = CAST(:cursor_value AS numeric) AND r.group_value > :cursor_tiebreaker)"
)
_AFTER_NULL = "HAVING {sort} IS NULL AND r.group_value > :cursor_tiebreaker"

# One row per bucket (`value`, `none`, `other`) and one for the whole window (`bucket` NULL):
# the buckets of the envelope and the totals from one pass over the source.
_TOTALS = """
    SELECT r.bucket,{sums},
           sum(r.folded_values)::bigint AS folded_values
    FROM {source}
    GROUP BY GROUPING SETS ((r.bucket), ())
"""

Cursor = tuple[Decimal | None, str]


def _having(sort: AttributionSort, after: Cursor | None) -> tuple[str, dict[str, Any]]:
    if after is None:
        return "", {}
    value, tiebreaker = after
    expression = _SORT_EXPRESSIONS[sort]
    if value is None:
        return _AFTER_NULL.format(sort=expression), {"cursor_tiebreaker": tiebreaker}
    having = _AFTER_NUMBER.format(sort=expression)
    return having, {"cursor_value": value, "cursor_tiebreaker": tiebreaker}


def _raw_source(
    project_id: uuid.UUID, kind: GroupKind, key: str | None, window: TimeWindow, env: str | None
) -> tuple[str, str, dict[str, Any]]:
    """The `WITH` prefix, the source and the binds of the raw read."""
    prefix = f"WITH {grouped_cte(kind, by_day=False, with_environment=env is not None)}"
    params: dict[str, Any] = {
        "project_id": project_id,
        "start": window.start,
        "end": window.end,
        "key": key,
    }
    if env is not None:
        params["environment"] = env
    return prefix, _RAW_SOURCE, params


def _rollup_source(
    project_id: uuid.UUID,
    kind: GroupKind,
    key: str | None,
    days: tuple[date, date],
    env: str | None,
) -> tuple[str, dict[str, Any]]:
    """The source and the binds of the rollup read over `[first_day, end_day)`."""
    # Equality on the key, not `IS NOT DISTINCT FROM`, so the natural key's index answers.
    key_filter = "r.group_key = CAST(:key AS text)" if kind == "metadata" else "r.group_key IS NULL"
    params: dict[str, Any] = {
        "project_id": project_id,
        "kind": kind,
        "key": key,
        "first_day": days[0],
        "end_day": days[1],
    }
    if env is not None:
        params["environment"] = env
    # `is not None`, as the bind above and `/traces`: `environment=` names the empty environment.
    env_filter = "\n      AND r.environment = :environment" if env is not None else ""
    source = _ROLLUP_SOURCE.format(key=key_filter, environment=env_filter)
    return source, params


async def read_raw_groups(
    db: AsyncSession,
    project_id: uuid.UUID,
    kind: GroupKind,
    key: str | None,
    window: TimeWindow,
    environment: str | None,
    sort: AttributionSort,
    limit: int,
    after: Cursor | None,
) -> list[Row[Any]]:
    """Up to `limit` values of the traces started in `window`, read live, after the cursor."""
    prefix, source, params = _raw_source(project_id, kind, key, window, environment)
    having, cursor_params = _having(sort, after)
    sql = prefix + _ITEMS.format(
        sums=_SUMS, source=source, where="WHERE ", having=having, sort=_SORT_EXPRESSIONS[sort]
    )
    result = await db.execute(text(sql), {**params, **cursor_params, "limit": limit})
    return list(result.all())


async def read_rollup_groups(
    db: AsyncSession,
    project_id: uuid.UUID,
    kind: GroupKind,
    key: str | None,
    days: tuple[date, date],
    environment: str | None,
    sort: AttributionSort,
    limit: int,
    after: Cursor | None,
) -> list[Row[Any]]:
    """Up to `limit` values of the rollup rows of `[first_day, end_day)`, after the cursor."""
    source, params = _rollup_source(project_id, kind, key, days, environment)
    having, cursor_params = _having(sort, after)
    sql = _ITEMS.format(
        sums=_SUMS, source=source, where="AND ", having=having, sort=_SORT_EXPRESSIONS[sort]
    )
    result = await db.execute(text(sql), {**params, **cursor_params, "limit": limit})
    return list(result.all())


async def read_totals(
    db: AsyncSession,
    project_id: uuid.UUID,
    kind: GroupKind,
    key: str | None,
    environment: str | None,
    *,
    window: TimeWindow | None = None,
    days: tuple[date, date] | None = None,
) -> list[Row[Any]]:
    """The buckets and the totals of the window, from the raw source (`window`) or the rollup
    source (`days`), in one query.

    One row per bucket present (`bucket` is `value`, `none` or `other`) and one with `bucket`
    NULL for the whole window; the last is always there, with NULL sums when nothing was found.
    """
    if window is not None:
        prefix, source, params = _raw_source(project_id, kind, key, window, environment)
    elif days is not None:
        prefix = ""
        source, params = _rollup_source(project_id, kind, key, days, environment)
    else:
        raise ValueError("read_totals needs a window or days")
    result = await db.execute(text(prefix + _TOTALS.format(sums=_SUMS, source=source)), params)
    return list(result.all())
