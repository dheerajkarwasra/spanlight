"""Traces, spans, sessions and the filter options offered for them."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.api.schemas.common import Money
from app.api.schemas.scores import ScoreSummaryOut
from app.db.models import SpanKind, SpanStatus
from app.ingest.error_class import ErrorClass


class TraceSummaryOut(BaseModel):
    trace_id: str
    name: str | None
    environment: str | None
    release: str | None
    external_user_id: str | None
    session_id: str | None
    tags: list[str]
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
    cost_usd: Money | None
    pricing_version: str | None
    time_to_first_token_ms: float | None
    input: Any
    output: Any
    attributes: dict[str, Any]
    truncated: bool
    finish_reason: str | None
    """Canonical: `stop`, `length`, `tool_calls`, `content_filter` or `other`; the raw value is
    the `finish_reason` attribute (or the provider's own attribute)."""
    prompt_version_id: uuid.UUID | None
    """The prompt version the SDK reported, as sent; the server may not know the id."""


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
