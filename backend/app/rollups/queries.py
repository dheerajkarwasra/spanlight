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
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import String, bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.window import TimeWindow
from app.rollups.buckets import BUCKET_COUNT, approx_percentile, merge
from app.rollups.compute import floor_hour
from app.rollups.coverage_reads import CoverageState, read_covered, read_covered_groups
from app.rollups.dirty import HOURLY
from app.rollups.watermark import RAW_TAIL_MAX as RAW_TAIL_MAX  # noqa: PLC0414 - public export.
from app.rollups.watermark import ROLLUP_GRACE as ROLLUP_GRACE  # noqa: PLC0414 - public export.
from app.rollups.watermark import read_split as read_split  # noqa: PLC0414 - public export.


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
    db: AsyncSession,
    project_id: uuid.UUID,
    window: TimeWindow,
    environment: str | None,
    coverage: CoverageState | None = None,
    *,
    parts: Sequence[tuple[TimeWindow, CoverageState]] = (),
) -> RollupSeries:
    """Read one merged row per hour: span metrics from the span rollups, traces from the trace
    rollups. An hour that only has traces (or only spans) still gets a row, with zeros for the
    missing side.

    With ``parts`` (windows inside ``window``), coverage is judged per part and recorded in its
    state instead of over the whole window in ``coverage``.
    """
    params = _params(project_id, window, environment)
    bounds: list[tuple[int, datetime, datetime, CoverageState | None]]
    bounds = [(g, floor_hour(w.start), w.end, state) for g, (w, state) in enumerate(parts)] or [
        (0, floor_hour(window.start), window.end, coverage)
    ]
    span_rows = await read_covered_groups(
        db, _SPAN_HOURS, params, project_id, bounds, relevant_consumers=HOURLY
    )
    trace_rows = await read_covered_groups(
        db, _TRACE_HOURS, params, project_id, bounds, relevant_consumers=HOURLY
    )

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


async def read_model_rollups(
    db: AsyncSession,
    project_id: uuid.UUID,
    window: TimeWindow,
    environment: str | None,
    coverage: CoverageState | None = None,
) -> list[ModelRollup]:
    """The llm calls per (provider, model), every pair: the caller merges, sorts and cuts."""
    rows = await read_covered(
        db,
        _MODELS,
        _params(project_id, window, environment),
        project_id,
        floor_hour(window.start),
        window.end,
        coverage,
        relevant_consumers=HOURLY,
    )
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
