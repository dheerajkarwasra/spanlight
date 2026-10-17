"""Provider health use-cases: health per provider, the error timeseries and sample failures.

A window is read in two halves (``app.alerts.metrics.plan_window``): the whole hours before
``floor_hour(now) - 1 h`` from the hourly rollups, the rest from raw spans, with the same
definitions on both sides (``app.providers.queries``), so every count is exact. Samples always
read raw spans, from at most the last 24 hours of the window.
"""

import uuid
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.metrics import WindowPlan, plan_window, rollup_split
from app.api.schemas.providers import (
    ErrorBucketOut,
    ErrorClassCountOut,
    ErrorSampleOut,
    HealthReportOut,
    IncidentRefOut,
    ProviderHealthOut,
    RateLimitKindCountOut,
    WindowOut,
)
from app.api.window import TimeWindow
from app.db.models import ProviderIncident
from app.provider_status import service as provider_status
from app.providers import queries, retry_queries, sample_queries, series
from app.providers.retry_queries import RetryReport
from app.rollups.error_queries import read_error_rollups
from app.rollups.filtered import RollupFilters

SAMPLE_WINDOW = timedelta(hours=24)
"""Samples come from at most the last 24 hours of the requested window."""

INCIDENTS_PER_PROVIDER = 20
"""The most incidents one provider's card lists for a window."""

RETRY_COST_NOTE = (
    "Retries are counted within each trace that started in the window, under the provider that "
    "served most of its LLM calls. Every provider's retry count and cost are unknown when a trace "
    "in the window was stored before retries were tracked (2026-10-15); a provider's cost is also "
    "unknown when one of its retried calls has no known price."
)

_MAX_NUMBER_TEXT = 32
_MAX_RETRY_AFTER_S = Decimal(86_400)
_MAX_ATTEMPTS_DIGITS = 4
_MAX_REQUEST_ID = 128


async def health(
    db: AsyncSession,
    project_id: uuid.UUID,
    window: TimeWindow,
    environment: str | None,
    now: datetime,
) -> HealthReportOut:
    """Calls, failures, retries and status page incidents per provider, busiest first."""
    plan = plan_window(window, rollup_split(now))
    counts = await _provider_counts(db, project_id, plan, environment)
    retries = await retry_queries.read_retry_totals(db, project_id, window, environment)
    order = series.busiest_first(set(counts) | set(retries.by_provider), counts)
    providers = [
        await _provider_out(db, counts.get(p) or series.ProviderCounts(p), retries, window)
        for p in order
    ]
    return HealthReportOut(
        window=WindowOut(start=window.start, end=window.end),
        approximate=plan.rollup is not None,
        providers=providers,
        retry_cost_note=RETRY_COST_NOTE,
    )


async def _provider_counts(
    db: AsyncSession, project_id: uuid.UUID, plan: WindowPlan, environment: str | None
) -> dict[str | None, series.ProviderCounts]:
    calls = await queries.read_raw_calls(db, project_id, plan.raw, environment)
    errors = await queries.read_raw_errors(db, project_id, plan.raw, environment)
    if plan.rollup is not None:
        calls += await queries.read_rollup_calls(db, project_id, plan.rollup, environment)
        hours = await read_error_rollups(
            db,
            project_id,
            TimeWindow(*plan.rollup),
            RollupFilters(environment=environment),
            provider_breakdown=True,
        )
        errors += [
            series.ErrorCount(
                h.provider, h.error_class, h.rate_limit_kind, h.errors, h.with_retry_after
            )
            for h in hours
        ]
    return series.provider_counts(calls, errors)


async def _provider_out(
    db: AsyncSession,
    counts: series.ProviderCounts,
    report: RetryReport,
    window: TimeWindow,
) -> ProviderHealthOut:
    rate = series.error_rate(counts.errors, counts.llm_calls)
    retries = report.for_provider(counts.provider)
    incidents: list[ProviderIncident] = []
    if counts.provider is not None:
        # Feeds store lowercase names; a span keeps its sender's case ("OpenAI").
        incidents = await provider_status.list_incidents(
            db,
            window=window,
            provider=counts.provider.lower(),
            api_only=True,
            limit=INCIDENTS_PER_PROVIDER,
            after=None,
        )
    return ProviderHealthOut(
        provider=counts.provider,
        llm_calls=counts.llm_calls,
        errors=counts.errors,
        error_rate=None if rate is None else series.decimal_text(rate),
        by_class=[ErrorClassCountOut(error_class=c, count=n) for c, n in counts.classes()],
        rate_limit_kinds=[
            RateLimitKindCountOut(kind=k.kind, count=k.count, with_retry_after=k.with_retry_after)
            for k in counts.rate_limit_kinds()
        ],
        retry_calls=retries.retry_calls,
        retry_cost_usd=retries.retry_cost_usd,
        retry_cost_unknown=retries.cost_unknown,
        incidents=[IncidentRefOut.model_validate(incident) for incident in incidents],
    )


async def timeseries(
    db: AsyncSession,
    project_id: uuid.UUID,
    window: TimeWindow,
    *,
    bucket: series.Bucket,
    environment: str | None,
    provider: str | None,
    now: datetime,
) -> list[ErrorBucketOut]:
    """Failed llm calls per bucket and error class; gapless, zero classes absent."""
    plan = plan_window(window, rollup_split(now))
    raw = await queries.read_raw_error_hours(
        db, project_id, plan.raw, environment=environment, provider=provider
    )
    hours: dict[tuple[datetime, str], int] = {}
    for row in raw:
        hours[(row.hour, row.error_class)] = hours.get((row.hour, row.error_class), 0) + row.errors
    rollup_hours: list[datetime] = []
    if plan.rollup is not None:
        rollup_hours = series.hours_between(*plan.rollup)
        rows = await read_error_rollups(
            db,
            project_id,
            TimeWindow(*plan.rollup),
            RollupFilters(environment=environment, provider=provider),
            provider_breakdown=False,
        )
        for h in rows:
            key = (h.bucket_start, h.error_class)
            hours[key] = hours.get(key, 0) + h.errors
    return [
        ErrorBucketOut(bucket_start=b.bucket_start, by_class=b.by_class, approximate=b.approximate)
        for b in series.bucket_series(window, bucket, hours, rollup_hours=rollup_hours)
    ]


def clamp_to_sample_window(window: TimeWindow) -> tuple[TimeWindow, bool]:
    """The window's last 24 hours, and whether that cut anything."""
    if window.length <= SAMPLE_WINDOW:
        return window, False
    return TimeWindow(window.end - SAMPLE_WINDOW, window.end), True


async def samples(
    db: AsyncSession,
    project_id: uuid.UUID,
    window: TimeWindow,
    filters: sample_queries.SampleFilters,
    *,
    limit: int,
    after: sample_queries.SampleCursor | None,
) -> tuple[list[ErrorSampleOut], sample_queries.SampleCursor | None]:
    """One page of failures, newest first, and the cursor of the next page (``None``: last)."""
    rows = await sample_queries.read_samples(
        db, project_id, window, filters, limit=limit + 1, after=after
    )
    page = rows[:limit]
    if len(rows) <= limit:
        return [_sample_out(row) for row in page], None
    last = page[-1]
    cursor = sample_queries.SampleCursor(last.started_at, last.span_id, last.trace_id)
    return [_sample_out(row) for row in page], cursor


def _sample_out(row: sample_queries.SampleRow) -> ErrorSampleOut:
    retry_after = _retry_after(row.gateway_retry_after)
    if retry_after is None:
        retry_after = _retry_after(row.provider_retry_after)
    request_id = row.request_id
    if request_id is not None and not 0 < len(request_id) <= _MAX_REQUEST_ID:
        request_id = None
    return ErrorSampleOut(
        trace_id=row.trace_id,
        span_id=row.span_id,
        started_at=row.started_at,
        model=row.model,
        status_message=row.status_message,
        error_class=row.error_class,
        rate_limit_kind=row.rate_limit_kind,
        provider_error_type=row.provider_error_type,
        provider_error_code=row.provider_error_code,
        retry_after_s=retry_after,
        attempts=_attempts(row.attempts),
        request_id=request_id,
    )


def _retry_after(raw: str | None) -> float | None:
    """A retry-after in seconds from attribute text; ``None`` unless a number in [0, 1 day].

    Attribute values are client-supplied: the text is bounded before it is converted.
    """
    if raw is None or len(raw) > _MAX_NUMBER_TEXT:
        return None
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return None
    if not value.is_finite() or not 0 <= value <= _MAX_RETRY_AFTER_S:
        return None
    return float(value)


def _attempts(raw: str | None) -> int | None:
    """An attempt count from attribute text; ``None`` unless a small non-negative integer."""
    if raw is None or not raw.isascii() or not raw.isdigit() or len(raw) > _MAX_ATTEMPTS_DIGITS:
        return None
    return int(raw)
