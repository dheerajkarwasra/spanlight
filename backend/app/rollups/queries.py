"""Read the hourly rollups for the metrics endpoints.

These queries return the same quantities as the raw span queries in ``app.metrics.queries`` and
use the same definitions:

* ``llm_calls``, errors, latency histograms and ``unpriced_calls`` come from rows of kind
  ``llm`` only.
* ``cost_usd`` and token totals come from rows of every kind. A cost is ``None`` when every
  contributing row is unpriced (``sum()`` of NULLs), never ``0``.
* ``traces`` come from ``trace_rollups_hourly``.
* An ``environment`` filter matches the rollup's environment dimension exactly, so rows with an
  unknown (NULL) environment only appear when no filter is given.

The window covers the hours whose ``bucket_start`` lies in ``[floor_hour(start), end)``. The first
and last hours are therefore read in full even when the window cuts through them.

Percentiles are not stored: each hour carries a latency histogram and
:func:`app.rollups.buckets.approx_percentile` estimates a percentile from the merged histogram,
which is why callers label these reads approximate.

The queries run in the caller's session and rely on its project binding for row-level security;
the explicit ``project_id`` filter is a second guard, not the only one.
"""

import uuid
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import String, bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.window import TimeWindow
from app.rollups.buckets import BUCKET_COUNT, approx_percentile, merge
from app.rollups.compute import floor_hour


def summed_histogram(column: str) -> str:
    """A ``bigint[]`` adding the llm rows' ``column`` histograms element-wise.

    SQL arrays are 1-based.
    """
    sums = ", ".join(
        f"coalesce(sum({column}[{position}]) FILTER (WHERE kind = 'llm'), 0)"
        for position in range(1, BUCKET_COUNT + 1)
    )
    return f"CAST(ARRAY[{sums}] AS bigint[])"


# `:environment` is NULL for "all environments".
_ROLLUP_FILTER = """
    WHERE project_id = :project_id
      AND bucket_start >= :start
      AND bucket_start < :end
      AND (:environment IS NULL OR environment = :environment)
"""

_SPAN_HOURS = text(
    f"""
    SELECT bucket_start,
           coalesce(sum(span_count) FILTER (WHERE kind = 'llm'), 0)      AS llm_calls,
           coalesce(sum(errors) FILTER (WHERE kind = 'llm'), 0)          AS llm_errors,
           coalesce(sum(unpriced_calls) FILTER (WHERE kind = 'llm'), 0)  AS unpriced_calls,
           sum(cost_usd)                                                 AS cost_usd,
           coalesce(sum(input_tokens), 0)                                AS input_tokens,
           coalesce(sum(output_tokens), 0)                               AS output_tokens,
           {summed_histogram("latency_buckets")}                        AS latency_buckets
    FROM span_rollups_hourly
    {_ROLLUP_FILTER}
    GROUP BY bucket_start
    ORDER BY bucket_start
    """
).bindparams(bindparam("environment", type_=String))

_TRACE_HOURS = text(
    f"""
    SELECT bucket_start,
           sum(traces) AS traces
    FROM trace_rollups_hourly
    {_ROLLUP_FILTER}
    GROUP BY bucket_start
    ORDER BY bucket_start
    """
).bindparams(bindparam("environment", type_=String))

_MODELS = text(
    f"""
    SELECT provider,
           model,
           sum(span_count)                          AS calls,
           sum(errors)                              AS errors,
           coalesce(sum(input_tokens), 0)           AS input_tokens,
           coalesce(sum(output_tokens), 0)          AS output_tokens,
           sum(cost_usd)                            AS cost_usd,
           {summed_histogram("latency_buckets")}   AS latency_buckets
    FROM span_rollups_hourly
    {_ROLLUP_FILTER}
      AND kind = 'llm'
    GROUP BY provider, model
    """
).bindparams(bindparam("environment", type_=String))

# Every rollup run rewrites a project's recent hours in one transaction, so the newest row carries
# the start of its last successful run (`computed_at` defaults to `now()`).
_WATERMARK = text(
    """
    SELECT computed_at FROM span_rollups_hourly
    WHERE project_id = :project_id
    ORDER BY bucket_start DESC
    LIMIT 1
    """
)

ROLLUP_GRACE = timedelta(minutes=10)
"""How long after an hour closes its spans may still arrive before the hour counts as complete."""

RAW_TAIL_MAX = timedelta(hours=24)
"""The longest raw tail a read accepts: rollups more than a day behind are read as they are."""


@dataclass(frozen=True)
class HourlyRollup:
    """The metrics of one UTC hour, or of several hours merged by :func:`combine`."""

    bucket_start: datetime
    traces: int
    llm_calls: int
    llm_errors: int
    unpriced_calls: int
    cost_usd: Decimal | None
    input_tokens: int
    output_tokens: int
    latency_buckets: list[int]

    @property
    def tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def error_rate(self) -> float | None:
        return self.llm_errors / self.llm_calls if self.llm_calls else None

    @property
    def p50_ms(self) -> float | None:
        return approx_percentile(self.latency_buckets, 0.5)

    @property
    def p95_ms(self) -> float | None:
        return approx_percentile(self.latency_buckets, 0.95)


@dataclass(frozen=True)
class RollupSeries:
    """Per-hour rollup rows for a window, oldest first. Hours without any row are absent."""

    hours: list[HourlyRollup]


@dataclass(frozen=True)
class ModelRollup:
    """The llm calls of one (provider, model) over the window, with the merged histogram."""

    provider: str | None
    model: str | None
    calls: int
    errors: int
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal | None
    latency_buckets: list[int]

    @property
    def p50_ms(self) -> float | None:
        return approx_percentile(self.latency_buckets, 0.5)

    @property
    def p95_ms(self) -> float | None:
        return approx_percentile(self.latency_buckets, 0.95)


def _params(project_id: uuid.UUID, window: TimeWindow, environment: str | None) -> dict[str, Any]:
    return {
        "project_id": project_id,
        "start": floor_hour(window.start),
        "end": window.end,
        "environment": environment,
    }


async def read_rollups(
    db: AsyncSession, project_id: uuid.UUID, window: TimeWindow, environment: str | None
) -> RollupSeries:
    """Read one merged row per hour: span metrics from the span rollups, traces from the trace
    rollups. An hour that only has traces (or only spans) still gets a row, with zeros for the
    missing side.
    """
    params = _params(project_id, window, environment)
    span_rows = (await db.execute(_SPAN_HOURS, params)).all()
    trace_rows = (await db.execute(_TRACE_HOURS, params)).all()

    hours: dict[datetime, HourlyRollup] = {
        row.bucket_start: HourlyRollup(
            bucket_start=row.bucket_start,
            traces=0,
            llm_calls=int(row.llm_calls),
            llm_errors=int(row.llm_errors),
            unpriced_calls=int(row.unpriced_calls),
            cost_usd=row.cost_usd,
            input_tokens=int(row.input_tokens),
            output_tokens=int(row.output_tokens),
            latency_buckets=[int(count) for count in row.latency_buckets],
        )
        for row in span_rows
    }
    for row in trace_rows:
        traces = int(row.traces)
        existing = hours.get(row.bucket_start)
        if existing is None:
            hours[row.bucket_start] = HourlyRollup(
                bucket_start=row.bucket_start,
                traces=traces,
                llm_calls=0,
                llm_errors=0,
                unpriced_calls=0,
                cost_usd=None,
                input_tokens=0,
                output_tokens=0,
                latency_buckets=[0] * BUCKET_COUNT,
            )
        else:
            hours[row.bucket_start] = replace(existing, traces=traces)
    return RollupSeries(hours=[hours[start] for start in sorted(hours)])


def combine(bucket_start: datetime, rows: Iterable[HourlyRollup]) -> HourlyRollup:
    """Merge hourly rows into one: counts and tokens add, histograms add element-wise, and the
    cost is ``None`` unless at least one row has a priced cost.
    """
    traces = llm_calls = llm_errors = unpriced_calls = input_tokens = output_tokens = 0
    cost_usd: Decimal | None = None
    latency_buckets = [0] * BUCKET_COUNT
    for row in rows:
        traces += row.traces
        llm_calls += row.llm_calls
        llm_errors += row.llm_errors
        unpriced_calls += row.unpriced_calls
        input_tokens += row.input_tokens
        output_tokens += row.output_tokens
        if row.cost_usd is not None:
            cost_usd = row.cost_usd if cost_usd is None else cost_usd + row.cost_usd
        latency_buckets = merge(latency_buckets, row.latency_buckets)
    return HourlyRollup(
        bucket_start=bucket_start,
        traces=traces,
        llm_calls=llm_calls,
        llm_errors=llm_errors,
        unpriced_calls=unpriced_calls,
        cost_usd=cost_usd,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency_buckets=latency_buckets,
    )


def group_hours(
    series: RollupSeries, bucket_of: Callable[[datetime], datetime]
) -> dict[datetime, HourlyRollup]:
    """Merge the series into coarser buckets; ``bucket_of`` maps an hour to its bucket start."""
    grouped: dict[datetime, list[HourlyRollup]] = defaultdict(list)
    for row in series.hours:
        grouped[bucket_of(row.bucket_start)].append(row)
    return {start: combine(start, rows) for start, rows in grouped.items()}


async def read_split(db: AsyncSession, project_id: uuid.UUID, now: datetime) -> datetime | None:
    """The first hour a metrics read takes from raw spans; every whole hour before it is read
    from the rollups.

    It follows the rollup job, not the clock: the hour that holds ``W - ROLLUP_GRACE``, where
    ``W`` is the newest rollup row's ``computed_at``, so every earlier hour closed at least the
    grace before the run that last rewrote it (the invariant in ``app.rollups.jobs``). A job that
    falls behind moves the split back and the read gets slower, never wrong, up to
    ``RAW_TAIL_MAX``. ``None`` when the project has no rollup row at all (new, or not rolled up
    yet): the whole window is read raw.
    """
    watermark = (await db.execute(_WATERMARK, {"project_id": project_id})).scalar_one_or_none()
    if watermark is None:
        return None
    return max(floor_hour(watermark - ROLLUP_GRACE), floor_hour(now - RAW_TAIL_MAX))


async def read_model_rollups(
    db: AsyncSession, project_id: uuid.UUID, window: TimeWindow, environment: str | None
) -> list[ModelRollup]:
    """The llm calls per (provider, model), every pair: the caller merges, sorts and cuts."""
    rows = (await db.execute(_MODELS, _params(project_id, window, environment))).all()
    return [
        ModelRollup(
            provider=row.provider,
            model=row.model,
            calls=int(row.calls),
            errors=int(row.errors),
            input_tokens=int(row.input_tokens),
            output_tokens=int(row.output_tokens),
            cost_usd=row.cost_usd,
            latency_buckets=[int(count) for count in row.latency_buckets],
        )
        for row in rows
    ]
