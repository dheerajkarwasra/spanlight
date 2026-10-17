"""The one metric read behind alert rules, budgets, the rule preview and the weekly digest.

Definitions follow the metrics endpoints (see ``docs/api-deviations.md``):

=========== ============================================ ========================================
Metric      Over                                         Empty window
=========== ============================================ ========================================
llm_calls   spans of kind ``llm``                        ``0``
error_calls llm errors                                   ``0``
error_rate  llm errors / llm calls                       ``None``
p95_ms      llm span duration                            ``None``
ttft_p95    llm time to first token, measured ones only  ``None`` (also when none measured one)
tokens      input + output tokens, every kind            ``0``
cost_usd    priced spans of every kind (a lower bound)   ``0``; ``None`` when spans exist and none
                                                         is priced
spend       as ``cost_usd``                              as ``cost_usd``
=========== ============================================ ========================================

With an ``error_class`` or ``rate_limit_kind`` filter, ``error_calls`` and ``error_rate`` count
only the failed llm calls of that class and kind (as the error rollup counts them: fault injections
left out) and ``error_rate`` still divides by every llm call. Those whole hours are read from
``error_rollups_hourly``, with the same split as every other count.

``spend`` is what budgets read. It is computed exactly like ``cost_usd``; a budget passes only the
filter of its own scope, never an environment, provider or model of its choosing.

Source split: the whole hours of a window that start strictly before ``floor_hour(now) - 1 h``
are read from the hourly rollups (complete by then); everything else is read from raw spans: the
partial hour at the window's start, the partial hour at its end when that is older than the
split, and everything from the split on. Counts, tokens and cost are therefore exact everywhere.
Only a percentile that merged any rollup hour is ``approximate``: it is estimated from the merged
histograms (the raw part is bucketed the same way). A ``source_key_id`` or ``external_user_id``
filter exists only on raw rows, so it makes the whole window read raw spans.

Values are ``Decimal``; percentiles arrive as ``float`` and are converted through ``str``.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.metrics_queries import LatencyColumn, RawAggregate, read_window_aggregates
from app.alerts.types import Metric, MetricFilters
from app.api.window import TimeWindow
from app.rollups.buckets import BUCKET_COUNT, approx_percentile, merge
from app.rollups.compute import HOUR, ceil_hour, floor_hour
from app.rollups.error_queries import errors_by_hour, read_error_rollups
from app.rollups.filtered import FilteredHour, RollupFilters, read_filtered_rollups

_P95 = 0.95

_LATENCY: dict[Metric, LatencyColumn] = {
    Metric.P95_MS: "duration_ms",
    Metric.TTFT_P95_MS: "time_to_first_token_ms",
}


@dataclass(frozen=True, slots=True)
class MetricValue:
    """A metric's value (``None`` when unknown) and whether it is a histogram estimate."""

    value: Decimal | None
    approximate: bool


@dataclass(frozen=True, slots=True)
class WindowPlan:
    """Where one window is read from: the rollup hours whose start is in ``rollup`` (start, end),
    each wholly inside the window, and the raw spans of the disjoint ranges ``raw``."""

    rollup: tuple[datetime, datetime] | None
    raw: tuple[TimeWindow, ...]


@dataclass(frozen=True, slots=True)
class _Totals:
    """A window's aggregates with the rollup and raw parts added together. ``class_errors`` is
    ``None`` unless the filters narrow the failed calls to an error class or rate-limit kind."""

    spans: int
    llm_calls: int
    llm_errors: int
    tokens: int
    cost_usd: Decimal | None
    histogram: list[int]
    class_errors: int | None

    @property
    def errors(self) -> int:
        """The failed calls the error metrics count."""
        return self.llm_errors if self.class_errors is None else self.class_errors


def rollup_split(now: datetime) -> datetime:
    """The first hour read from raw spans: every earlier hour has a complete rollup."""
    return floor_hour(now) - HOUR


def plan_window(window: TimeWindow, split: datetime | None) -> WindowPlan:
    """Rollups for the whole hours before ``split``; raw spans for the rest (at most two ranges:
    the partial head hour, and the partial tail hour or everything from the split on)."""
    if split is None or window.start >= split:
        return WindowPlan(None, (window,))
    first = ceil_hour(window.start)
    last = floor_hour(min(window.end, split))
    if first >= last:
        return WindowPlan(None, (window,))
    head = TimeWindow(window.start, first)
    tail = TimeWindow(last, window.end)
    return WindowPlan((first, last), tuple(r for r in (head, tail) if r.start < r.end))


async def metric_value(
    db: AsyncSession,
    project_id: uuid.UUID,
    metric: Metric,
    window: TimeWindow,
    filters: MetricFilters,
    *,
    now: datetime | None = None,
) -> MetricValue:
    """The metric over one window. ``now`` (default: the current time) sets the source split."""
    values = await metric_series(db, project_id, metric, [window], filters, now=now)
    return values[0]


async def metric_series(
    db: AsyncSession,
    project_id: uuid.UUID,
    metric: Metric,
    windows: Sequence[TimeWindow],
    filters: MetricFilters,
    *,
    now: datetime | None = None,
) -> list[MetricValue]:
    """The metric over each window, in order, from one rollup read and one raw read.

    Each value equals what :func:`metric_value` returns for that window alone.
    """
    if now is not None and now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if not windows:
        return []
    split = None if filters.forces_raw else rollup_split(now or datetime.now(UTC))
    plans = [plan_window(window, split) for window in windows]
    hours = await _read_hours(db, project_id, plans, filters)
    latency = _LATENCY.get(metric)
    any_rollup = any(plan.rollup is not None for plan in plans)
    raws = await read_window_aggregates(
        db,
        project_id,
        [plan.raw for plan in plans],
        filters,
        latency,
        with_histogram=any_rollup,
    )
    return [_value(metric, plan, hours, raw) for plan, raw in zip(plans, raws, strict=True)]


async def _read_hours(
    db: AsyncSession, project_id: uuid.UUID, plans: Sequence[WindowPlan], filters: MetricFilters
) -> list[FilteredHour]:
    """Every rollup hour any plan needs, in one read over their hull."""
    ranges = [plan.rollup for plan in plans if plan.rollup is not None]
    if not ranges:
        return []
    rollup_filters = RollupFilters(filters.environment, filters.provider, filters.model)
    hull = TimeWindow(min(start for start, _ in ranges), max(end for _, end in ranges))
    hours = await read_filtered_rollups(db, project_id, hull, rollup_filters)
    if not filters.narrows_errors:
        return hours
    rows = await read_error_rollups(db, project_id, hull, rollup_filters, provider_breakdown=False)
    counts = errors_by_hour(rows, filters.error_class, filters.rate_limit_kind)
    return [replace(hour, class_errors=counts.get(hour.bucket_start, 0)) for hour in hours]


def _value(
    metric: Metric, plan: WindowPlan, hours: Sequence[FilteredHour], raw: RawAggregate
) -> MetricValue:
    if plan.rollup is None:
        totals = _add(metric, [], raw)
        return MetricValue(_compute(metric, totals, raw.percentile), approximate=False)
    start, end = plan.rollup
    inside = [hour for hour in hours if start <= hour.bucket_start < end]
    totals = _add(metric, inside, raw)
    percentile = approx_percentile(totals.histogram, _P95)
    approximate = metric in _LATENCY
    return MetricValue(_compute(metric, totals, percentile), approximate=approximate)


def _add(metric: Metric, hours: Sequence[FilteredHour], raw: RawAggregate) -> _Totals:
    """Sum the rollup hours and the raw part; the histogram is the metric's latency column."""
    histogram = raw.histogram if raw.histogram is not None else [0] * BUCKET_COUNT
    cost, class_errors = raw.cost_usd, raw.class_errors
    spans, calls, errors, tokens = (
        raw.spans,
        raw.llm_calls,
        raw.llm_errors,
        raw.input_tokens + raw.output_tokens,
    )
    for hour in hours:
        spans += hour.spans
        calls += hour.llm_calls
        errors += hour.llm_errors
        tokens += hour.tokens
        if class_errors is not None:
            class_errors += hour.class_errors or 0
        if hour.cost_usd is not None:
            cost = hour.cost_usd if cost is None else cost + hour.cost_usd
        if metric is Metric.P95_MS:
            histogram = merge(histogram, hour.latency_buckets)
        elif metric is Metric.TTFT_P95_MS:
            histogram = merge(histogram, hour.ttft_buckets)
    return _Totals(spans, calls, errors, tokens, cost, histogram, class_errors)


def _compute(metric: Metric, totals: _Totals, percentile: float | None) -> Decimal | None:
    """The metric from summed aggregates and the window's p95 of its latency column."""
    match metric:
        case Metric.LLM_CALLS:
            return Decimal(totals.llm_calls)
        case Metric.TOKENS:
            return Decimal(totals.tokens)
        case Metric.ERROR_CALLS:
            return Decimal(totals.errors)
        case Metric.ERROR_RATE:
            if totals.llm_calls == 0:
                return None
            return Decimal(totals.errors) / Decimal(totals.llm_calls)
        case Metric.P95_MS | Metric.TTFT_P95_MS:
            return None if percentile is None else Decimal(str(percentile))
        case Metric.COST_USD | Metric.SPEND:
            if totals.spans == 0:
                return Decimal(0)
            return totals.cost_usd


class MetricCache:
    """Memoises metric reads within one job run, keyed by ``(project, metric, window, filters)``.

    Every read uses the run's ``now``, which is why it is not part of the key. Create one cache
    per run and drop it afterwards.
    """

    def __init__(self, now: datetime) -> None:
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        self._now = now
        self._values: dict[tuple[uuid.UUID, Metric, TimeWindow, MetricFilters], MetricValue] = {}

    async def value(
        self,
        db: AsyncSession,
        project_id: uuid.UUID,
        metric: Metric,
        window: TimeWindow,
        filters: MetricFilters,
    ) -> MetricValue:
        return (await self.series(db, project_id, metric, [window], filters))[0]

    async def series(
        self,
        db: AsyncSession,
        project_id: uuid.UUID,
        metric: Metric,
        windows: Sequence[TimeWindow],
        filters: MetricFilters,
    ) -> list[MetricValue]:
        """Like :func:`metric_series`; only the windows not read before reach the database."""
        missing = list(
            dict.fromkeys(
                w for w in windows if (project_id, metric, w, filters) not in self._values
            )
        )
        if missing:
            values = await metric_series(db, project_id, metric, missing, filters, now=self._now)
            for window, value in zip(missing, values, strict=True):
                self._values[(project_id, metric, window, filters)] = value
        return [self._values[(project_id, metric, window, filters)] for window in windows]
