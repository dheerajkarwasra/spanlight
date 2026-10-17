"""Calls and failures per provider, from the hourly span rollups and from raw spans.

The health and timeseries routes read a window in two halves (``app.alerts.metrics.plan_window``):
the whole hours before the split from the rollups, the rest from raw spans. The definitions match
on both sides:

* an llm call is a span of kind ``llm`` (``span_rollups_hourly`` rows of kind ``llm``);
* a failure is a span matching ``FAILED_LLM_SPAN``, the predicate ``error_rollups_hourly`` is
  built with, so fault injections from the gateway's lab are left out of both halves;
* ``with_retry_after`` counts the failures that carried a provider ``retry-after`` as a number,
  with the error rollup's own predicate (``WITH_RETRY_AFTER``);
* ``environment`` is the trace's, so filtering by it joins ``traces`` on the raw side.

The raw ranges of a window (at most two, disjoint) are passed as arrays and joined to ``spans``
with ``unnest``, so each one is an index range scan. Statements are assembled from fixed
fragments according to which filters are set; values only travel as bound parameters.

The queries run in the caller's session and rely on its project binding for row-level security;
the explicit ``project_id`` filter is a second guard, not the only one.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from functools import cache
from typing import Any

from sqlalchemy import DateTime, String, TextClause, bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.window import TimeWindow
from app.providers.series import ErrorCount
from app.rollups.error_compute import FAILED_LLM_SPAN, WITH_RETRY_AFTER

_ROLLUP_CALLS = text(
    """
    SELECT provider, sum(span_count) AS llm_calls
    FROM span_rollups_hourly
    WHERE project_id = :project_id
      AND bucket_start >= :start
      AND bucket_start < :end
      AND kind = 'llm'
      AND (:environment IS NULL OR environment = :environment)
    GROUP BY provider
    """
).bindparams(bindparam("environment", type_=String))

_CONDITIONS = {"environment": "t.environment = :environment", "provider": "s.provider = :provider"}
_TRACE_JOIN = "JOIN traces AS t ON t.project_id = s.project_id AND t.trace_id = s.trace_id"

_SELECTS = {
    "calls": ("s.provider, count(*) AS llm_calls", "s.kind = 'llm'", "1"),
    "errors": (
        "s.provider, s.error_class, s.rate_limit_kind, count(*) AS errors, "
        f"count(*) FILTER (WHERE {WITH_RETRY_AFTER}) AS with_retry_after",
        FAILED_LLM_SPAN,
        "1, 2, 3",
    ),
    "hours": (
        "date_trunc('hour', s.started_at, 'UTC') AS hour, s.error_class, count(*) AS errors",
        FAILED_LLM_SPAN,
        "1, 2",
    ),
}


@dataclass(frozen=True, slots=True)
class ErrorHourCount:
    """Failed llm calls of one UTC hour and error class."""

    hour: datetime
    error_class: str
    errors: int


@cache
def _raw_statement(select: str, filters: frozenset[str]) -> TextClause:
    columns, predicate, group_by = _SELECTS[select]
    join = _TRACE_JOIN if "environment" in filters else ""
    conditions = "".join(f"\n      AND {_CONDITIONS[name]}" for name in sorted(filters))
    return text(
        f"""
        SELECT {columns}
        FROM unnest(CAST(:starts AS timestamptz[]), CAST(:ends AS timestamptz[]))
             AS w(start_at, end_at)
        JOIN spans AS s
          ON s.project_id = :project_id
         AND s.started_at >= w.start_at
         AND s.started_at < w.end_at
        {join}
        WHERE {predicate}{conditions}
        GROUP BY {group_by}
        """
    ).bindparams(
        bindparam("starts", type_=ARRAY(DateTime(timezone=True))),
        bindparam("ends", type_=ARRAY(DateTime(timezone=True))),
    )


async def _read_raw(
    db: AsyncSession,
    select: str,
    project_id: uuid.UUID,
    ranges: Sequence[TimeWindow],
    filters: dict[str, str | None],
) -> Sequence[Any]:
    ranges = [r for r in ranges if r.start < r.end]
    if not ranges:
        return []
    set_filters = {name: value for name, value in filters.items() if value is not None}
    params = {
        **set_filters,
        "project_id": project_id,
        "starts": [r.start for r in ranges],
        "ends": [r.end for r in ranges],
    }
    statement = _raw_statement(select, frozenset(set_filters))
    return (await db.execute(statement, params)).all()


async def read_rollup_calls(
    db: AsyncSession,
    project_id: uuid.UUID,
    hours: tuple[datetime, datetime],
    environment: str | None,
) -> list[tuple[str | None, int]]:
    """llm calls per provider over the rollup hours starting in ``[hours[0], hours[1])``."""
    params = {
        "project_id": project_id,
        "start": hours[0],
        "end": hours[1],
        "environment": environment,
    }
    rows = (await db.execute(_ROLLUP_CALLS, params)).all()
    return [(row.provider, int(row.llm_calls)) for row in rows]


async def read_raw_calls(
    db: AsyncSession, project_id: uuid.UUID, ranges: Sequence[TimeWindow], environment: str | None
) -> list[tuple[str | None, int]]:
    """llm calls per provider over the raw ``ranges``."""
    rows = await _read_raw(db, "calls", project_id, ranges, {"environment": environment})
    return [(row.provider, int(row.llm_calls)) for row in rows]


async def read_raw_errors(
    db: AsyncSession, project_id: uuid.UUID, ranges: Sequence[TimeWindow], environment: str | None
) -> list[ErrorCount]:
    """Failed llm calls per provider, error class and rate-limit kind over the raw ``ranges``."""
    rows = await _read_raw(db, "errors", project_id, ranges, {"environment": environment})
    return [
        ErrorCount(
            provider=row.provider,
            error_class=row.error_class,
            rate_limit_kind=row.rate_limit_kind,
            errors=int(row.errors),
            with_retry_after=int(row.with_retry_after),
        )
        for row in rows
    ]


async def read_raw_error_hours(
    db: AsyncSession,
    project_id: uuid.UUID,
    ranges: Sequence[TimeWindow],
    *,
    environment: str | None,
    provider: str | None,
) -> list[ErrorHourCount]:
    """Failed llm calls per UTC hour and error class over the raw ``ranges``."""
    filters = {"environment": environment, "provider": provider}
    rows = await _read_raw(db, "hours", project_id, ranges, filters)
    return [ErrorHourCount(row.hour, row.error_class, int(row.errors)) for row in rows]
