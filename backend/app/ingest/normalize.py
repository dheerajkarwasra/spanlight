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
from app.ingest.request_hash import request_hash
from app.ingest.schemas import SpanIn
from app.pricing.cost import PriceBook

MAX_PAYLOAD_BYTES = 32 * 1024
MAX_ATTRIBUTES_BYTES = 64 * 1024
MAX_SPAN_AGE = timedelta(days=90)  # the longest retention a project can configure
MAX_CLOCK_SKEW = timedelta(minutes=10)


class SpanRejectedError(Exception):
    """A span failed a semantic check; the message is reported to the client."""


@dataclass(frozen=True)
class TraceFields:
    name: str | None
    environment: str | None
    release: str | None
    external_user_id: str | None
    session_id: str | None
    tags: tuple[str, ...]


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
    started_at: datetime
    ended_at: datetime
    provider: str | None
    model: str | None
    input_tokens: int | None
    output_tokens: int | None
    cached_tokens: int | None
    cost_usd: Decimal | None
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
    tokens = _usage(span)
    attributes = _attributes(span)
    stored_input, stored_output, truncated = _payloads(span, capture_payloads)
    cost_usd, pricing_version = _cost(span, prices, tokens)
    status_message = redact_text(span.status_message) if span.status_message else None
    return NormalizedSpan(
        trace_id=span.trace_id,
        span_id=span.span_id,
        parent_span_id=span.parent_span_id,
        kind=span.kind,
        name=span.name,
        status=span.status,
        status_message=status_message,
        error_class=classify_error(span.status, status_message, attributes),
        started_at=span.start_time,
        ended_at=span.end_time,
        provider=span.provider,
        model=span.model,
        input_tokens=tokens[0],
        output_tokens=tokens[1],
        cached_tokens=tokens[2],
        cost_usd=cost_usd,
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
        trace=_trace_fields(span),
    )


_Tokens = tuple[int | None, int | None, int | None]


def _usage(span: SpanIn) -> _Tokens:
    """Input, output and cached tokens; cached ones are part of the input and cannot exceed it."""
    usage = span.usage
    if usage is None:
        return None, None, None
    cached, total = usage.cached_tokens, usage.input_tokens
    if cached is not None and total is not None and cached > total:
        raise SpanRejectedError("usage.cached_tokens: cannot exceed usage.input_tokens")
    return total, usage.output_tokens, cached


def _cost(span: SpanIn, prices: PriceBook, tokens: _Tokens) -> tuple[Decimal | None, str | None]:
    """The span's cost and the price version used; both None when it cannot be priced."""
    input_tokens, output_tokens, cached_tokens = tokens
    cost = prices.cost(
        provider=span.provider,
        model=span.model,
        at=span.start_time,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_tokens=cached_tokens,
    )
    return (cost.cost_usd, cost.pricing_version) if cost else (None, None)


def _payloads(span: SpanIn, capture_payloads: bool) -> tuple[Any, Any, bool]:
    """The input and output to store, and whether either was truncated."""
    if not capture_payloads:
        return None, None, False
    stored_input, input_truncated = _prepare_payload(span.input)
    stored_output, output_truncated = _prepare_payload(span.output)
    return stored_input, stored_output, input_truncated or output_truncated


def _attributes(span: SpanIn) -> dict[str, Any]:
    """The redacted attributes, holding the span's raw `finish_reason` when it sent one.

    The `finish_reason` column holds the canonical value; the provider's own stays here.
    """
    attributes: dict[str, Any] = redact_json(span.attributes)
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


def _trace_fields(span: SpanIn) -> TraceFields:
    trace = span.trace
    if trace is None:
        return TraceFields(None, None, None, None, None, ())
    return TraceFields(
        name=trace.name,
        environment=trace.environment,
        release=trace.release,
        external_user_id=trace.user_id,
        session_id=trace.session_id,
        tags=tuple(trace.tags or ()),
    )


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
