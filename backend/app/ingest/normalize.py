"""Turn validated spans into storage-ready rows.

Normalization covers semantic checks that a schema cannot express (time
ordering, plausible timestamps), payload redaction and truncation, the
per-project payload capture toggle, and cost calculation.
"""

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from app.core.redact import redact_json, redact_text
from app.db.models import SpanKind, SpanStatus
from app.ingest.error_class import ErrorClass, classify_error
from app.ingest.finish_reason import canonical_finish_reason, raw_finish_reason
from app.ingest.provider_attributes import clean_provider_attributes
from app.ingest.provider_fields import provider_columns
from app.ingest.rate_limit_kind import RateLimitKind
from app.ingest.request_hash import request_hash
from app.ingest.schemas import SpanIn
from app.ingest.trace_fields import TraceFields, trace_fields
from app.pricing.cost import PriceBook

MAX_PAYLOAD_BYTES = 32 * 1024
MAX_ATTRIBUTES_BYTES = 64 * 1024
MAX_SPAN_AGE = timedelta(days=90)  # the longest retention a project can configure
MAX_CLOCK_SKEW = timedelta(minutes=10)
# `spans.cost_usd` is numeric(14, 8): a cost must stay below a million dollars to be stored.
MAX_STORED_COST = Decimal(1_000_000)


class SpanRejectedError(Exception):
    """A span failed a semantic check; the message is reported to the client."""


@dataclass(frozen=True)
class NormalizedSpan:
    trace_id: str
    span_id: str
    parent_span_id: str | None
    kind: SpanKind
    name: str
    status: SpanStatus
    status_message: str | None
    error_class: ErrorClass | None
    provider_error_type: str | None
    provider_error_code: str | None
    rate_limit_kind: RateLimitKind | None
    started_at: datetime
    ended_at: datetime
    provider: str | None
    model: str | None
    input_tokens: int | None
    output_tokens: int | None
    cached_tokens: int | None
    cache_write_tokens: int | None
    cache_write_1h_tokens: int | None
    service_tier: str | None
    web_search_requests: int | None
    cost_usd: Decimal | None
    fee_usd: Decimal | None
    pricing_version: str | None
    time_to_first_token_ms: float | None
    input: Any
    output: Any
    attributes: dict[str, Any]
    truncated: bool
    request_hash: str | None
    finish_reason: str | None
    prompt_version_id: uuid.UUID | None
    trace: TraceFields


def normalize_span(
    span: SpanIn, *, capture_payloads: bool, prices: PriceBook, now: datetime
) -> NormalizedSpan:
    _check_span(span, now)
    try:
        trace = trace_fields(span)
    except ValueError as exc:  # invalid trace metadata, with its field path
        raise SpanRejectedError(str(exc)) from None
    usage = _usage(span)
    attributes = _attributes(span)
    stored_input, stored_output, truncated = _payloads(span, capture_payloads)
    cost_usd, fee_usd, pricing_version = _cost(span, prices, usage)
    status_message = redact_text(span.status_message) if span.status_message else None
    error_class = classify_error(span.status, status_message, attributes)
    return NormalizedSpan(
        trace_id=span.trace_id,
        span_id=span.span_id,
        parent_span_id=span.parent_span_id,
        kind=span.kind,
        name=span.name,
        status=span.status,
        status_message=status_message,
        error_class=error_class,
        **provider_columns(span, error_class, status_message, attributes),
        started_at=span.start_time,
        ended_at=span.end_time,
        provider=span.provider,
        model=span.model,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cached_tokens=usage.cached_tokens,
        cache_write_tokens=usage.cache_write_tokens,
        cache_write_1h_tokens=usage.cache_write_1h_tokens,
        service_tier=usage.service_tier,
        web_search_requests=usage.web_search_requests,
        cost_usd=cost_usd,
        fee_usd=fee_usd,
        pricing_version=pricing_version,
        time_to_first_token_ms=(
            float(span.time_to_first_token_ms) if span.time_to_first_token_ms is not None else None
        ),
        input=stored_input,
        output=stored_output,
        attributes=attributes,
        truncated=truncated,
        # From the input as received, before the payload capture setting drops it.
        request_hash=span.request_hash or request_hash(span.model, span.input),
        finish_reason=canonical_finish_reason(raw_finish_reason(span.finish_reason, attributes)),
        prompt_version_id=span.prompt_version_id,
        trace=trace,
    )


@dataclass(frozen=True)
class _Usage:
    """The checked usage of a span; every field None when it sent no usage."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_tokens: int | None = None
    cache_write_tokens: int | None = None
    cache_write_1h_tokens: int | None = None
    service_tier: str | None = None
    web_search_requests: int | None = None


def _usage(span: SpanIn) -> _Usage:
    """The usage as sent, once the parts of the prompt are known to fit inside it.

    `input_tokens` is the whole prompt, cache reads and writes included, so reads plus writes
    cannot exceed it, and the 1-hour writes are a subset of all writes. A 1-hour count without a
    total is refused here: the database check compares with NULL and would let it through.
    """
    usage = span.usage
    if usage is None:
        return _Usage()
    total, reads, writes, writes_1h = (
        usage.input_tokens,
        usage.cached_tokens,
        usage.cache_write_tokens,
        usage.cache_write_1h_tokens,
    )
    if reads is not None and total is not None and reads > total:
        raise SpanRejectedError("usage.cached_tokens: cannot exceed usage.input_tokens")
    if writes is not None and total is not None and (reads or 0) + writes > total:
        raise SpanRejectedError(
            "usage.cache_write_tokens: cached_tokens + cache_write_tokens cannot exceed "
            "usage.input_tokens"
        )
    if writes_1h is not None:
        if writes is None:
            raise SpanRejectedError(
                "usage.cache_write_1h_tokens: requires usage.cache_write_tokens"
            )
        if writes_1h > writes:
            raise SpanRejectedError(
                "usage.cache_write_1h_tokens: cannot exceed usage.cache_write_tokens"
            )
    searches = usage.server_tool_use.web_search_requests if usage.server_tool_use else None
    return _Usage(
        total, usage.output_tokens, reads, writes, writes_1h, usage.service_tier, searches
    )


def _cost(
    span: SpanIn, prices: PriceBook, usage: _Usage
) -> tuple[Decimal | None, Decimal | None, str | None]:
    """The span's cost, its fee part and the price version used; each None when unknown.

    A fee is known without the token cost (a cut stream that still counted searches): the fee is
    then stored while the cost and version stay None, never a partial total.
    """
    cost = prices.cost(
        provider=span.provider,
        model=span.model,
        at=span.start_time,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cached_tokens=usage.cached_tokens,
        cache_write_tokens=usage.cache_write_tokens,
        cache_write_1h_tokens=usage.cache_write_1h_tokens,
        service_tier=usage.service_tier,
        web_search_requests=usage.web_search_requests,
    )
    if cost is None:
        return None, None, None
    if cost.cost_usd is not None and cost.cost_usd >= MAX_STORED_COST:
        # Only an organization's own price override can price a call this high.
        raise SpanRejectedError("usage: the cost at this model's price is too large to store")
    return cost.cost_usd, cost.fee_usd, cost.pricing_version


def _payloads(span: SpanIn, capture_payloads: bool) -> tuple[Any, Any, bool]:
    """The input and output to store, and whether either was truncated."""
    if not capture_payloads:
        return None, None, False
    stored_input, input_truncated = _prepare_payload(span.input)
    stored_output, output_truncated = _prepare_payload(span.output)
    return stored_input, stored_output, input_truncated or output_truncated or span.truncated


def prepare_span_payloads(
    span: dict[str, Any], *, capture_payloads: bool
) -> tuple[dict[str, Any], bool]:
    """A raw span with `input` and `output` already as they will be stored, and the flag.

    For a caller that holds spans before writing them (the gateway recorder's queue): what it
    holds is then bounded by `MAX_PAYLOAD_BYTES` per payload and already redacted. Without
    payload capture both are dropped. Pass the flag as `IngestTarget.prepared_truncated`, so
    ingestion stores the payloads as they are instead of preparing them a second time.
    """
    if not capture_payloads:
        return {**span, "input": None, "output": None}, False
    stored_input, input_truncated = _prepare_payload(span.get("input"))
    stored_output, output_truncated = _prepare_payload(span.get("output"))
    prepared = {**span, "input": stored_input, "output": stored_output}
    return prepared, input_truncated or output_truncated


def _attributes(span: SpanIn) -> dict[str, Any]:
    """The redacted attributes, holding the span's raw `finish_reason` when it sent one.

    The `finish_reason` column holds the canonical value; the provider's own stays here. Malformed
    `provider.*` values are dropped whoever sent them.
    """
    attributes = clean_provider_attributes(redact_json(span.attributes))
    if span.finish_reason and attributes.get("finish_reason") is None:
        attributes["finish_reason"] = redact_text(span.finish_reason)
    if _json_size(attributes) > MAX_ATTRIBUTES_BYTES:
        raise SpanRejectedError(f"attributes: exceed {MAX_ATTRIBUTES_BYTES} bytes")
    return attributes


def _check_span(span: SpanIn, now: datetime) -> None:
    """The semantic checks a schema cannot express: time ordering, plausible times, parentage."""
    if span.end_time < span.start_time:
        raise SpanRejectedError("end_time: must not be before start_time")
    if span.start_time < now - MAX_SPAN_AGE:
        raise SpanRejectedError("start_time: older than the maximum retention of 90 days")
    if span.end_time > now + MAX_CLOCK_SKEW:
        raise SpanRejectedError("end_time: is in the future")
    if span.parent_span_id == span.span_id:
        raise SpanRejectedError("parent_span_id: a span cannot be its own parent")


def _json_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())


def _prepare_payload(value: Any) -> tuple[Any, bool]:
    """Redact, then truncate to MAX_PAYLOAD_BYTES.

    Redaction runs first so a secret straddling the cut can never survive in
    partial form. An oversized payload is replaced by a string holding the
    start of its JSON text, and the span is flagged `truncated`.
    """
    if value is None:
        return None, False
    redacted = redact_json(value)
    serialized = json.dumps(redacted, ensure_ascii=False, separators=(",", ":"))
    encoded = serialized.encode()
    if len(encoded) <= MAX_PAYLOAD_BYTES:
        return redacted, False
    preview = encoded[:MAX_PAYLOAD_BYTES].decode(errors="ignore")
    return preview, True
