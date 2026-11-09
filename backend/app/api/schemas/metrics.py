"""Project-level KPIs, time series and per-model breakdowns."""

from datetime import date, datetime

from pydantic import BaseModel

from app.api.schemas.common import AggregateMetadata, Money


class KpisOut(AggregateMetadata):
    traces: int | None
    llm_calls: int | None
    error_rate: float | None
    p50_ms: float | None
    p95_ms: float | None
    cost_usd: Money | None
    unpriced_calls: int | None
    input_tokens: int | None
    output_tokens: int | None


class OverviewOut(BaseModel):
    current: KpisOut
    previous: KpisOut
    approximate: bool


class TimeseriesPointOut(AggregateMetadata):
    bucket_start: datetime
    llm_calls: int | None
    errors: int | None
    p95_ms: float | None
    cost_usd: Money | None
    tokens: int | None
    approximate: bool


class ModelMetricsOut(AggregateMetadata):
    provider: str | None
    model: str | None
    calls: int
    errors: int
    p50_ms: float | None
    p95_ms: float | None
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: Money | None
    approximate: bool


class TokenMixOut(AggregateMetadata):
    """LLM tokens by price class over the whole UTC days `first_day`..`last_day`, all
    environments. Every figure is null when the project has no cost rollup in those days."""

    first_day: date
    last_day: date
    uncached_input_tokens: int | None
    output_tokens: int | None
    cache_read_tokens: int | None
    cache_write_tokens: int | None
    fee_usd: Money | None
    cost_usd: Money | None
