"""Project metrics use-cases: KPI overview, time series and per-model breakdown.

Definitions (shared with the frontend, see docs/api-deviations.md):
- `llm_calls`, `error_rate`, latency percentiles and `unpriced_calls` are over
  spans of kind `llm` that started inside the window.
- `cost_usd` and token totals are over all spans in the window, matching the
  trace rollups. `cost_usd` is null when no span in the window was priced.
- `traces` counts traces that started inside the window.
- An `environment` filter applies to the trace the span belongs to.

Source: a window of any length is planned into the whole hours before the project's rollup split
(``app.rollups.queries.read_split``), read from the hourly rollups, and the rest, at most two
ranges, read from the raw spans (``app.metrics.queries``). Both halves use the same definitions
and the ranges are disjoint, so counts, tokens and cost are exact sums. When a window reads any
rollup hour, its percentiles are estimated from the merged latency histograms (the raw part is
bucketed the same way) and the response says `approximate: true`; otherwise they are exact.
"""

import uuid
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import Row
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.metrics import WindowPlan, plan_window
from app.api.schemas import KpisOut, ModelMetricsOut, OverviewOut, TimeseriesPointOut
from app.api.window import TimeWindow
from app.core.observability import METRICS_RAW_TAIL
from app.metrics import queries
from app.rollups.buckets import BUCKET_COUNT, merge
from app.rollups.compute import HOUR, floor_hour
from app.rollups.coverage_reads import CoverageState
from app.rollups.queries import (
    HourlyRollup,
    ModelRollup,
    RollupSeries,
    add_known,
    combine,
    group_hours,
    known_int,
    read_model_rollups,
    read_rollups,
    read_split,
)

Bucket = Literal["hour", "day"]

MODEL_LIMIT = 100


async def overview(
    db: AsyncSession, project_id: uuid.UUID, window: TimeWindow, environment: str | None
) -> OverviewOut:
    """KPIs for the window and for the window of equal length right before it."""
    [(current, current_estimated), (previous, previous_estimated)] = await _windows_kpis(
        db, project_id, [window, window.previous()], environment
    )
    return OverviewOut(
        current=current, previous=previous, approximate=current_estimated or previous_estimated
    )


async def _windows_kpis(
    db: AsyncSession, project_id: uuid.UUID, windows: Sequence[TimeWindow], environment: str | None
) -> list[tuple[KpisOut, bool]]:
    """Each window's KPIs and whether they are estimated, read together (one statement per query
    family) but judged per window: coverage, its fallback and the latency form are each window's
    own, so a window's result equals reading it alone."""
    coverages = [CoverageState() for _ in windows]
    plans = await _plans(db, project_id, windows)
    estimated = [plan.rollup is not None for plan in plans]
    parts = [
        (TimeWindow(*plan.rollup), coverage)
        for plan, coverage in zip(plans, coverages, strict=True)
        if plan.rollup is not None
    ]
    hours = await _rollup_hours(db, project_id, plans, environment, parts=parts)
    raw = [plan.raw for plan in plans]
    spans = await queries.read_span_kpis(db, project_id, raw, environment, estimated, coverages)
    traces = await queries.read_trace_counts(db, project_id, raw, environment, coverages)
    results: list[tuple[KpisOut, bool]] = []
    for i, window in enumerate(windows):
        coverage, plan, row, count = coverages[i], plans[i], spans[i], traces[i]
        rolled = estimated[i]
        window_hours = hours
        if coverage.unavailable:
            window_hours = (await read_rollups(db, project_id, window, environment, coverage)).hours
            plan = WindowPlan((floor_hour(window.start), window.end), ())
            row, count, rolled = None, 0, True
        result = _kpis(window.start, plan, window_hours, row, count, rolled)
        result.aggregate_status = coverage.status
        result.oldest_pending_at = coverage.oldest_pending_at
        if coverage.unavailable and not window_hours:
            for field in KpisOut.model_fields:
                if field not in {"aggregate_status", "oldest_pending_at"}:
                    setattr(result, field, None)
        results.append((result, rolled))
    return results


async def timeseries(
    db: AsyncSession,
    project_id: uuid.UUID,
    window: TimeWindow,
    environment: str | None,
    bucket: Bucket,
    coverage: CoverageState | None = None,
) -> list[TimeseriesPointOut]:
    """Gapless points, one per UTC bucket from the one holding ``window.start`` up to its end.

    A point that holds a rollup hour has its p95 estimated and says so; a point of raw spans
    only keeps the exact p95.
    """
    coverage = coverage or CoverageState()
    [plan] = await _plans(db, project_id, [window])
    hours = await _rollup_hours(db, project_id, [plan], environment, coverage)
    raw_rows = await queries.read_timeseries(
        db, project_id, plan.raw, environment, bucket, plan.rollup is not None, coverage
    )
    if coverage.unavailable:
        hours = (await read_rollups(db, project_id, window, environment, coverage)).hours
        raw_rows = []
    rolled = group_hours(RollupSeries(hours), lambda hour: _bucket_start(hour, bucket))
    raw = {row.bucket_start: row for row in raw_rows}
    step = HOUR if bucket == "hour" else timedelta(days=1)
    points: list[TimeseriesPointOut] = []
    current = _bucket_start(floor_hour(window.start), bucket)
    while current < window.end:
        rolled_part, raw_row = rolled.get(current), raw.get(current)
        parts = [part for part in (rolled_part, _raw_hour(current, raw_row)) if part is not None]
        total = combine(current, parts)
        if rolled_part is None:
            p95 = raw_row.p95_ms if raw_row is not None else None
        else:
            p95 = total.p95_ms
        points.append(
            TimeseriesPointOut(
                bucket_start=current,
                llm_calls=None if coverage.unavailable and not parts else total.llm_calls,
                errors=None if coverage.unavailable and not parts else total.llm_errors,
                p95_ms=p95,
                cost_usd=total.cost_usd,
                tokens=None if coverage.unavailable and not parts else total.tokens,
                approximate=rolled_part is not None,
                **coverage.metadata(),
            )
        )
        current += step
    return points


async def models(
    db: AsyncSession,
    project_id: uuid.UUID,
    window: TimeWindow,
    environment: str | None,
    coverage: CoverageState | None = None,
) -> list[ModelMetricsOut]:
    """Calls, errors, latency, tokens and cost per provider and model, busiest first, at most 100.

    The rollup and raw groups are merged before sorting and cutting, so a model ranked low in one
    half is still placed by its total.
    """
    [plan] = await _plans(db, project_id, [window])
    estimated = plan.rollup is not None
    coverage = coverage or CoverageState()
    raw_rows = await queries.read_models(db, project_id, plan.raw, environment, estimated, coverage)
    if plan.rollup is None:
        rows: list[Any] = list(raw_rows)
    else:
        rows = await _merged_models(db, project_id, plan.rollup, raw_rows, environment, coverage)
    if coverage.unavailable:
        rows = await read_model_rollups(db, project_id, window, environment, coverage)
        estimated = True
    rows.sort(
        key=lambda row: (
            -row.calls,
            row.provider is None,
            row.provider or "",
            row.model is None,
            row.model or "",
        )
    )
    return [
        ModelMetricsOut(
            provider=row.provider,
            model=row.model,
            calls=row.calls,
            errors=row.errors,
            p50_ms=row.p50_ms,
            p95_ms=row.p95_ms,
            input_tokens=row.input_tokens,
            output_tokens=row.output_tokens,
            cost_usd=row.cost_usd,
            approximate=estimated,
            **coverage.metadata(),
        )
        for row in rows[:MODEL_LIMIT]
    ]


async def _plans(
    db: AsyncSession, project_id: uuid.UUID, windows: Sequence[TimeWindow]
) -> list[WindowPlan]:
    """Plan each window against the project's rollup split."""
    now = datetime.now(UTC)
    split = await read_split(db, project_id, now)
    if split is not None:
        METRICS_RAW_TAIL.observe((now - split).total_seconds())
    return [plan_window(window, split) for window in windows]


async def _rollup_hours(
    db: AsyncSession,
    project_id: uuid.UUID,
    plans: Sequence[WindowPlan],
    environment: str | None,
    coverage: CoverageState | None = None,
    *,
    parts: Sequence[tuple[TimeWindow, CoverageState]] = (),
) -> list[HourlyRollup]:
    """Every rollup hour the plans need, from one read over their hull (coverage per part when
    ``parts`` is given, see :func:`read_rollups`)."""
    ranges = [plan.rollup for plan in plans if plan.rollup is not None]
    if not ranges:
        return []
    hull = TimeWindow(min(start for start, _ in ranges), max(end for _, end in ranges))
    return (await read_rollups(db, project_id, hull, environment, coverage, parts=parts)).hours


def _kpis(
    start: datetime,
    plan: WindowPlan,
    hours: Sequence[HourlyRollup],
    row: Row[Any] | None,
    traces: int,
    estimated: bool,
) -> KpisOut:
    """One window's KPIs: its rollup hours plus its raw part."""
    parts = _inside(plan, hours)
    raw = _raw_hour(start, row, traces)
    if raw is not None:
        parts.append(raw)
    total = combine(start, parts)
    if estimated:
        p50, p95 = total.p50_ms, total.p95_ms
    else:
        p50, p95 = (row.p50_ms, row.p95_ms) if row is not None else (None, None)
    return KpisOut(
        traces=total.traces,
        llm_calls=total.llm_calls,
        error_rate=total.error_rate,
        p50_ms=p50,
        p95_ms=p95,
        cost_usd=total.cost_usd,
        unpriced_calls=total.unpriced_calls,
        input_tokens=total.input_tokens,
        output_tokens=total.output_tokens,
    )


def _inside(plan: WindowPlan, hours: Sequence[HourlyRollup]) -> list[HourlyRollup]:
    if plan.rollup is None:
        return []
    first, last = plan.rollup
    return [hour for hour in hours if first <= hour.bucket_start < last]


def _raw_hour(bucket_start: datetime, row: Row[Any] | None, traces: int = 0) -> HourlyRollup | None:
    """A raw aggregate row in the shape of a rollup hour, so :func:`combine` adds both."""
    if row is None:
        return None if traces == 0 else replace(combine(bucket_start, []), traces=traces)
    return HourlyRollup(
        bucket_start=bucket_start,
        traces=traces,
        llm_calls=int(row.llm_calls),
        llm_errors=int(row.llm_errors),
        unpriced_calls=int(row.unpriced_calls),
        cost_usd=row.cost_usd,
        input_tokens=known_int(row.input_tokens),
        output_tokens=known_int(row.output_tokens),
        latency_buckets=_buckets(row),
    )


def _buckets(row: Row[Any]) -> list[int]:
    histogram = getattr(row, "latency_buckets", None)
    return [int(count) for count in histogram] if histogram is not None else [0] * BUCKET_COUNT


async def _merged_models(
    db: AsyncSession,
    project_id: uuid.UUID,
    rollup: tuple[datetime, datetime],
    raw_rows: Sequence[Row[Any]],
    environment: str | None,
    coverage: CoverageState | None = None,
) -> list[Any]:
    """The rollup and raw groups of each (provider, model) added together."""
    merged: dict[tuple[str | None, str | None], ModelRollup] = {
        (row.provider, row.model): row
        for row in await read_model_rollups(
            db, project_id, TimeWindow(*rollup), environment, coverage
        )
    }
    for row in raw_rows:
        key = (row.provider, row.model)
        raw = ModelRollup(
            provider=row.provider,
            model=row.model,
            calls=int(row.calls),
            errors=int(row.errors),
            input_tokens=known_int(row.input_tokens),
            output_tokens=known_int(row.output_tokens),
            cost_usd=row.cost_usd,
            latency_buckets=_buckets(row),
        )
        known = merged.get(key)
        merged[key] = raw if known is None else _add_models(known, raw)
    return list(merged.values())


def _add_models(a: ModelRollup, b: ModelRollup) -> ModelRollup:
    cost: Decimal | None = a.cost_usd
    if b.cost_usd is not None:
        cost = b.cost_usd if cost is None else cost + b.cost_usd
    return ModelRollup(
        provider=a.provider,
        model=a.model,
        calls=a.calls + b.calls,
        errors=a.errors + b.errors,
        input_tokens=add_known(a.input_tokens, b.input_tokens),
        output_tokens=add_known(a.output_tokens, b.output_tokens),
        cost_usd=cost,
        latency_buckets=merge(a.latency_buckets, b.latency_buckets),
    )


def _bucket_start(hour: datetime, bucket: Bucket) -> datetime:
    """The UTC bucket that holds an hour: the hour itself, or midnight of its day."""
    utc_hour = hour.astimezone(UTC)
    return utc_hour if bucket == "hour" else utc_hour.replace(hour=0)
