"""Traces, spans, sessions and the filter options offered for them."""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, computed_field

from app.api.schemas.common import Money, Page
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
    input_tokens: int | None
    """Summed over the trace's calls; null when none of them reported it."""
    output_tokens: int | None
    cost_usd: Money | None
    has_unpriced: bool
    models: list[str]
    error_message: str | None
    """Earliest failed span's status message; null if nothing failed or no message was sent."""
    error_class: ErrorClass | None
    """The same span's error class; null if nothing failed or it failed before classification."""
    root_received: bool | None
    """Whether a span without a parent was stored. False while only child spans have arrived: the
    root may still be running, or was sent elsewhere; duration, tokens and cost cover the spans
    received. Null when it could not be read."""
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
    input_preview: str | None = None
    """At most 200 characters of readable text from the root span's input, else the first llm
    span's input. Filled only by the trace list with `preview=true`; null otherwise or when no
    input was captured."""
    output_preview: str | None = None
    """The same from the root span's output, else the last llm span's output."""


class TraceBucketOut(BaseModel):
    bucket_start: datetime
    """The bucket's start in UTC: an hour, or midnight of a day."""
    traces: int
    errors: int
    """Of those traces, the ones with at least one failed span."""


class TraceHistogramOut(BaseModel):
    bucket: Literal["hour", "day"]
    """Hours for a window of 48 hours or less, else days."""
    buckets: list[TraceBucketOut]
    """Oldest first; buckets without a trace are left out."""


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
    """True when trace detail (or session turns) left `input` and `output` out (null) because the
    payloads passed the response's 8 MiB budget; `GET .../traces/{trace_id}/spans/{span_id}`
    returns them. Always false on that endpoint."""
    finish_reason: str | None
    """Canonical: `stop`, `length`, `tool_calls`, `content_filter` or `other`; the raw value is
    the `finish_reason` attribute (or the provider's own attribute)."""
    prompt_version_id: uuid.UUID | None
    """The prompt version the SDK reported, as sent; the server may not know the id."""
    usage_rollup: bool
    """True when a descendant span carries its own usage; excluded from every total."""
    request_hash: str | None
    """32 hex characters identifying what the model was asked (model and input, as
    `traces.retry_calls` groups calls); null when the span has no input."""

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


class TraceTotalsDeltaOut(BaseModel):
    """Trace B minus trace A."""

    duration_ms: float
    input_tokens: int | None
    """Null when either trace's total is unknown; so is `output_tokens`."""
    output_tokens: int | None
    cost_usd: Money | None
    """Null when either trace has an unpriced call or no cost."""
    span_count: int
    error_count: int


class StepSpanOut(BaseModel):
    """One side of a compared step: the span's stored values, nulls as stored."""

    span_id: str
    kind: SpanKind
    status: SpanStatus
    model: str | None
    duration_ms: float
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: Money | None


class StepOut(BaseModel):
    """A span of trace A and its counterpart in trace B, paired by name and occurrence."""

    name: str
    occurrence: int
    """1 for the first span of this name in its trace (in start order), 2 for the second."""
    a: StepSpanOut | None
    """Null when the step is only in trace B."""
    b: StepSpanOut | None
    """Null when the step is only in trace A."""
    duration_ms: float | None
    """B minus A; null unless both sides exist. So are the other deltas, which are also null
    when either side did not report the value."""
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: Money | None
    input_changed: bool | None
    """Whether the span was asked something else. Null when a side is missing or captured no
    input, or when the inputs match but one of the spans was truncated at ingest (input or
    output)."""
    output_changed: bool | None
    """The same for the output. Same input with a changed output is a nondeterministic step."""


class TraceCompareOut(BaseModel):
    a: TraceSummaryOut
    b: TraceSummaryOut
    totals: TraceTotalsDeltaOut
    steps: list[StepOut]
    """In trace A's order; a step only in B follows the last step before it that A also has."""
    truncated: bool
    """A trace has more than 2,000 spans: only the first 2,000 of each, by start, are compared."""


class SessionSummaryOut(BaseModel):
    session_id: str
    trace_count: int
    first_at: datetime
    last_at: datetime
    cost_usd: Money | None
    # A trace in the session has an unpriced call, so a known `cost_usd` is a lower bound.
    has_unpriced: bool
    error_count: int


class SessionListItemOut(SessionSummaryOut):
    """A `GET /sessions` row: the summary a user's recent sessions show, and more."""

    external_user_id: str | None
    """The user id of the session's traces when they all name the same one (traces without one
    left out), else null."""
    user_count: int
    """Distinct user ids among the session's traces."""
    input_tokens: int | None
    """Null when no trace of the session reported it; so is `output_tokens`."""
    output_tokens: int | None
    duration_ms: float
    """From the first trace's start to the last trace's end."""
    last_trace_id: str | None
    """The newest trace (latest start), the first turn `GET /sessions/{id}/turns` returns; null
    only when the session's traces were deleted while the page was read."""
    last_input_preview: str | None = None
    """The newest trace's `input_preview` (as `GET /traces` picks it); null without
    `preview=true`."""


class SessionPage(Page[SessionListItemOut]):
    cost_p99_usd: Money | None
    """The 99th percentile of session cost over every session of the window (and environment),
    whatever the search, on the first page (no `cursor`); null on later pages and while fewer than
    20 sessions have a known cost."""


class SessionTurnOut(BaseModel):
    """One trace of a session and the spans its conversation turn is read from."""

    trace: TraceSummaryOut
    """The same item `GET /traces` returns."""
    spans: list[SpanOut]
    """At most 4 distinct spans, in start order: the root (no parent, or a parent not stored), the
    first llm span with an input, the last llm span with an output, and the earliest failed
    span."""
    tool_calls: int
    """The trace's tool spans, all of them (not only those in `spans`)."""


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
