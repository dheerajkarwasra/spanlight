"""Traces, spans, sessions and the filter options offered for them."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, computed_field

from app.api.schemas.common import Money
from app.api.schemas.scores import ScoreSummaryOut
from app.db.models import SpanKind, SpanStatus
from app.ingest.error_class import ErrorClass
from app.ingest.rate_limit_kind import RateLimitKind
from app.ingest.usage_state import UsageState, usage_state
from app.pricing.tiers import ServiceTier


class TraceSummaryOut(BaseModel):
    trace_id: str
    name: str | None
    environment: str | None
    release: str | None
    external_user_id: str | None
    session_id: str | None
    tags: list[str]
    metadata: dict[str, str]
    """The trace's metadata as stored: at most 32 keys, every value a string."""
    started_at: datetime
    ended_at: datetime
    duration_ms: float
    span_count: int
    error_count: int
    input_tokens: int
    output_tokens: int
    cost_usd: Money | None
    has_unpriced: bool
    models: list[str]
    error_message: str | None
    """Earliest failed span's status message; null if nothing failed or no message was sent."""
    error_class: ErrorClass | None
    """The same span's error class; null if nothing failed or it failed before classification."""
    has_scores: bool
    retry_calls: int | None
    """The trace's superseded llm calls: calls of the same request (request hash) that a later
    call of it replaced; cache hits and fault-injected calls are not counted. Null for a trace
    not recomputed since retries were introduced (stored before 2026-10-15)."""
    retry_cost_usd: Money | None
    """The cost of those calls. Null when any of them is unpriced, or when `retry_calls` is
    null."""
    scores: list[ScoreSummaryOut]
    """The latest score of each name, most recently scored first; at most 10 names. The full
    list is `GET /traces/{trace_id}/scores`."""


class SpanOut(BaseModel):
    span_id: str
    parent_span_id: str | None
    kind: SpanKind
    name: str
    status: SpanStatus
    status_message: str | None
    error_class: ErrorClass | None
    provider_error_type: str | None
    """The error type the provider's envelope named (`provider.error.type`), at most 64
    characters; null when the span succeeded or none was reported."""
    provider_error_code: str | None
    """The error code the provider's envelope carried (`provider.error.code`), at most 64
    characters; null when the span succeeded or none was reported."""
    rate_limit_kind: RateLimitKind | None
    """Which capacity limit refused the call: `requests`, `tokens`, `spend_cap`, `acceleration`,
    `overloaded`, `gateway` (Spanlight's own per-key limiter) or `unknown`. Null when the span
    was not a capacity refusal."""
    started_at: datetime
    ended_at: datetime
    duration_ms: float
    provider: str | None
    model: str | None
    input_tokens: int | None
    output_tokens: int | None
    cached_tokens: int | None
    """Prompt tokens read from the provider's cache; part of `input_tokens`."""
    cache_write_tokens: int | None
    """Prompt tokens written to the provider's cache, every TTL; part of `input_tokens`. Null
    when the span did not report them."""
    cache_write_1h_tokens: int | None
    """The part of `cache_write_tokens` written with the 1-hour TTL; null when not reported, in
    which case every write was priced as 5-minute."""
    service_tier: ServiceTier | None
    """The tier the provider billed at; null when not reported, in which case the span was priced
    at the standard rate."""
    web_search_requests: int | None
    """Web searches the provider ran for the call; null when not reported."""
    cost_usd: Money | None
    """The whole cost of the span: token cost plus `fee_usd`. Null when the token cost is
    unknown, even when the fee is known."""
    fee_usd: Money | None
    """The non-token part of `cost_usd` (web search fees); null when no fee was priced."""
    pricing_version: str | None
    time_to_first_token_ms: float | None
    input: Any
    output: Any
    attributes: dict[str, Any]
    truncated: bool
    payload_omitted: bool = False
    """True when trace detail left `input` and `output` out (null) because the trace's payloads
    passed the response's 8 MiB budget; `GET .../traces/{trace_id}/spans/{span_id}` returns
    them. Always false on that endpoint."""
    finish_reason: str | None
    """Canonical: `stop`, `length`, `tool_calls`, `content_filter` or `other`; the raw value is
    the `finish_reason` attribute (or the provider's own attribute)."""
    prompt_version_id: uuid.UUID | None
    """The prompt version the SDK reported, as sent; the server may not know the id."""
    usage_rollup: bool
    """True when a descendant span carries its own usage; excluded from every total."""

    @computed_field  # type: ignore[prop-decorator]
    @property
    def usage_state(self) -> UsageState:
        """Why the span has or lacks a cost, derived from the stored columns on every read."""
        return usage_state(
            kind=self.kind,
            model=self.model,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            cost_usd=self.cost_usd,
            usage_rollup=self.usage_rollup,
        )


class TraceDetailOut(TraceSummaryOut):
    spans: list[SpanOut]


class SessionSummaryOut(BaseModel):
    session_id: str
    trace_count: int
    first_at: datetime
    last_at: datetime
    cost_usd: Money | None
    error_count: int


class FiltersOut(BaseModel):
    environments: list[str]
    releases: list[str]
    models: list[str]
    tags: list[str]
    score_names: list[str]
    metadata_keys: list[str]
    """Keys seen on traces started in the last 7 days; at most 100, sorted."""
    trace_names: list[str]
    """Names of traces started in the last 7 days; at most 100, sorted."""
