"""Project-level KPIs, time series and per-model breakdowns."""

from datetime import date, datetime

from pydantic import BaseModel

from app.api.schemas.common import Money


class KpisOut(BaseModel):
    traces: int
    llm_calls: int
    error_rate: float | None
    p50_ms: float | None
    p95_ms: float | None
    cost_usd: Money | None
    unpriced_calls: int
    input_tokens: int
    output_tokens: int


class OverviewOut(BaseModel):
    current: KpisOut
    previous: KpisOut
    approximate: bool


class TimeseriesPointOut(BaseModel):
    bucket_start: datetime
    llm_calls: int
    errors: int
    p95_ms: float | None
    cost_usd: Money | None
    tokens: int
    approximate: bool


class ModelMetricsOut(BaseModel):
    provider: str | None
    model: str | None
    calls: int
    errors: int
    p50_ms: float | None
    p95_ms: float | None
    input_tokens: int
    output_tokens: int
    cost_usd: Money | None
    approximate: bool


class TokenMixOut(BaseModel):
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
