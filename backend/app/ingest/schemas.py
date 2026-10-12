"""Validation schemas for the native ingestion format (`POST /v1/traces`).

Each span is validated independently so one bad span is reported in
`rejected` instead of failing the whole batch.
"""

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

MAX_SPANS_PER_BATCH = 1000
MAX_BODY_BYTES = 5 * 1024 * 1024
MAX_TOKEN_COUNT = 2_000_000_000  # spans store token counts as int4

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
        return parsed.astimezone(UTC)
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


def _valid_hash_or_none(value: object) -> str | None:
    """A client's request hash, lowercased, or None when it is malformed.

    The hash is an optional hint: a bad one is dropped and the server computes its own, rather
    than the span being rejected.
    """
    if isinstance(value, str) and REQUEST_HASH.fullmatch(value.lower()):
        return value.lower()
    return None


RequestHash = Annotated[str | None, BeforeValidator(_valid_hash_or_none)]


Text64 = Annotated[StrictStr, StringConstraints(min_length=1, max_length=64)]
Text128 = Annotated[StrictStr, StringConstraints(min_length=1, max_length=128)]
Text256 = Annotated[StrictStr, StringConstraints(min_length=1, max_length=256)]


class UsageIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    input_tokens: TokenCount | None = None
    output_tokens: TokenCount | None = None
    cached_tokens: TokenCount | None = None


class TraceIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: Text256 | None = None
    environment: Text64 | None = None
    release: Text128 | None = None
    user_id: Text256 | None = None
    session_id: Text256 | None = None
    tags: list[Text64] | None = Field(default=None, max_length=20)


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
    provider: Text128 | None = None
    model: Text256 | None = None
    usage: UsageIn | None = None
    time_to_first_token_ms: Annotated[StrictFloat | StrictInt, Field(ge=0)] | None = None
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


class IngestBatchIn(BaseModel):
    """The envelope only; spans are validated one by one afterwards."""

    model_config = ConfigDict(extra="ignore")

    spans: list[Any]


class RejectedSpan(BaseModel):
    index: int
    span_id: str | None
    reason: str


class IngestResponse(BaseModel):
    accepted: int
    rejected: list[RejectedSpan]
