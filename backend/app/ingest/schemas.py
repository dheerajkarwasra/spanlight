"""Validation schemas for the native ingestion format (`POST /v1/traces`).

Each span is validated independently so one bad span is reported in
`rejected` instead of failing the whole batch.
"""

import sys
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictFloat,
    StrictInt,
    StrictStr,
    StringConstraints,
)

from app.db.models import SpanKind, SpanStatus
from app.ingest.request_hash import REQUEST_HASH
from app.pricing.tiers import ServiceTier

MAX_SPANS_PER_BATCH = 1000
MAX_BODY_BYTES = 5 * 1024 * 1024
MAX_TOKEN_COUNT = 2_000_000_000  # spans store token counts as int4
MAX_WEB_SEARCH_REQUESTS = 100_000

# Unix nanoseconds must fall between 1970 and roughly 2262 (int64 range).
_MAX_UNIX_NANOS = 2**63 - 1


def _parse_timestamp(value: object) -> datetime:
    """Accept an ISO-8601 string with an offset, or integer unix nanoseconds."""
    if isinstance(value, bool):
        raise ValueError("timestamp must be an ISO-8601 string or integer unix nanoseconds")
    if isinstance(value, int):
        if not 0 < value <= _MAX_UNIX_NANOS:
            raise ValueError("unix nanosecond timestamp is out of range")
        seconds, nanos = divmod(value, 1_000_000_000)
        return datetime.fromtimestamp(seconds, tz=UTC).replace(microsecond=nanos // 1000)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("timestamp is not valid ISO-8601") from exc
        if parsed.tzinfo is None:
            raise ValueError("timestamp must include a UTC offset")
        try:
            return parsed.astimezone(UTC)
        except OverflowError as exc:  # year 1 or 9999, pushed past the range by its offset
            raise ValueError("timestamp is out of range") from exc
    raise ValueError("timestamp must be an ISO-8601 string or integer unix nanoseconds")


def _lowercase(value: object) -> object:
    return value.lower() if isinstance(value, str) else value


Timestamp = Annotated[datetime, BeforeValidator(_parse_timestamp)]


def _nonzero(value: str) -> str:
    if not value.strip("0"):
        raise ValueError("identifier must not be all zeros")
    return value


TraceId = Annotated[
    StrictStr,
    BeforeValidator(_lowercase),
    StringConstraints(pattern=r"^[0-9a-f]{32}$"),
    AfterValidator(_nonzero),
]
SpanId = Annotated[
    StrictStr,
    BeforeValidator(_lowercase),
    StringConstraints(pattern=r"^[0-9a-f]{16}$"),
    AfterValidator(_nonzero),
]
TokenCount = Annotated[StrictInt, Field(ge=0, le=MAX_TOKEN_COUNT)]


def _fits_double(value: float) -> float:
    """`value`, if a double precision column can hold it; a JSON integer can be far larger."""
    if value > sys.float_info.max:
        raise ValueError("number is too large to store")
    return value


_Double = AfterValidator(_fits_double)


def _valid_hash_or_none(value: object) -> str | None:
    """A client's request hash, lowercased, or None when it is malformed.

    The hash is an optional hint: a bad one is dropped and the server computes its own, rather
    than the span being rejected.
    """
    if isinstance(value, str) and REQUEST_HASH.fullmatch(value.lower()):
        return value.lower()
    return None


RequestHash = Annotated[str | None, BeforeValidator(_valid_hash_or_none)]


def _strip_model(value: object) -> object:
    """The model name without surrounding whitespace, or None when nothing else is left.

    Case is kept: the stored name is what pricing, alert, rollup and budget filters all compare,
    so one rule decides whether a span matches (`app.core.model_match`).
    """
    if not isinstance(value, str):
        return value
    return value.strip() or None


def _strip_provider(value: object) -> object:
    """The provider name without surrounding whitespace, or None when nothing else is left.

    A blank provider is no provider: it gets no server-tool fee rather than matching a rate by
    accident, and is stored as unknown.
    """
    if not isinstance(value, str):
        return value
    return value.strip() or None


Text64 = Annotated[StrictStr, StringConstraints(min_length=1, max_length=64)]
Text128 = Annotated[StrictStr, StringConstraints(min_length=1, max_length=128)]
Text256 = Annotated[StrictStr, StringConstraints(min_length=1, max_length=256)]


# Strict on this route: any other spelling rejects the span. The other paths normalize the
# provider's own spelling (`app.pricing.tiers.normalize_tier`) before sending this shape.
ServiceTierIn = ServiceTier


class ServerToolUseIn(BaseModel):
    """Server-side tool calls the provider billed apart from tokens. Unpriced tools are ignored."""

    model_config = ConfigDict(extra="ignore")

    web_search_requests: Annotated[StrictInt, Field(ge=0, le=MAX_WEB_SEARCH_REQUESTS)] | None = None


class UsageIn(BaseModel):
    """Token usage. `input_tokens` is the whole prompt: uncached, cache reads and cache writes.

    `cached_tokens` is the part read from the provider's prompt cache and `cache_write_tokens` the
    part written to it; `cache_write_1h_tokens` is the subset of those writes made with the 1-hour
    TTL (absent: every write is 5-minute). The sums are checked in `normalize._usage`.

    `service_tier` is the tier the provider billed at, one of four values; absent, the span is
    priced as standard. `server_tool_use.web_search_requests` prices the provider's web search fee.
    """

    model_config = ConfigDict(extra="ignore")

    input_tokens: TokenCount | None = None
    output_tokens: TokenCount | None = None
    cached_tokens: TokenCount | None = None
    cache_write_tokens: TokenCount | None = None
    cache_write_1h_tokens: TokenCount | None = None
    service_tier: ServiceTierIn | None = None
    server_tool_use: ServerToolUseIn | None = None


class TraceIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: Text256 | None = None
    environment: Text64 | None = None
    release: Text128 | None = None
    user_id: Text256 | None = None
    session_id: Text256 | None = None
    tags: list[Text64] | None = Field(default=None, max_length=20)
    # An object of at most 32 entries. Typed `Any` on purpose: every rule, the object check
    # included, is applied in `trace_fields.trace_fields`, so a rejection names the field exactly
    # as the metadata contract does (spec §4.2).
    metadata: Any = None


class SpanIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    trace_id: TraceId
    span_id: SpanId
    parent_span_id: SpanId | None = None
    name: Text256
    kind: SpanKind = SpanKind.OTHER
    status: SpanStatus = SpanStatus.UNSET
    status_message: Annotated[StrictStr, StringConstraints(max_length=4096)] | None = None
    start_time: Timestamp
    end_time: Timestamp
    provider: Annotated[Text128 | None, BeforeValidator(_strip_provider)] = None
    model: Annotated[Text256 | None, BeforeValidator(_strip_model)] = None
    usage: UsageIn | None = None
    time_to_first_token_ms: Annotated[StrictFloat | StrictInt, Field(ge=0), _Double] | None = None
    input: Any = None
    output: Any = None
    attributes: dict[str, Any] = Field(default_factory=dict, max_length=256)
    trace: TraceIn | None = None
    # Computed client side by the SDK (`app.ingest.request_hash`); wins over the server's.
    request_hash: RequestHash = None
    # The provider's raw value; the server stores its canonical form (`app.ingest.finish_reason`).
    finish_reason: Text64 | None = None
    # The prompt version the SDK fetched for this call. Not checked against the server's
    # versions: an id it has not seen (or one deleted since) is stored as sent.
    prompt_version_id: UUID | None = None


# A cumulative SDK counter: what a Postgres bigint holds, never negative.
CounterTotal = Annotated[StrictInt, Field(ge=0, lt=2**63)]
ExporterId = Annotated[StrictStr, StringConstraints(pattern=r"^[0-9a-f]{32}$")]


class ClientCountersIn(BaseModel):
    """The SDK exporter's lifetime loss counters, sent beside the spans (`client`).

    The bounds match migration 0703's CHECKs, so a value the table would refuse is refused here
    first and the batch's transaction never fails on it.
    """

    model_config = ConfigDict(extra="ignore")

    sdk: Text64
    version: Annotated[StrictStr, StringConstraints(min_length=1, max_length=32)]
    exporter_id: ExporterId
    dropped_total: CounterTotal
    failed_total: CounterTotal


class IngestBatchIn(BaseModel):
    """The envelope only; spans are validated one by one afterwards.

    `client` is validated on its own (`app.telemetry_losses.service.check_client`): a malformed
    one is ignored, never a reason to refuse the batch.
    """

    model_config = ConfigDict(extra="ignore")

    spans: list[Any]
    client: Any = None


class RejectedSpan(BaseModel):
    index: int
    span_id: str | None
    reason: str


class IngestResponse(BaseModel):
    accepted: int
    rejected: list[RejectedSpan]
