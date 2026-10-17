"""Batch API results recorded as spans, shared by every provider's batch recorder.

A batch result arrives long after its request was sent and carries no request, so each
result becomes one ``llm`` span recorded at retrieval time with a duration of zero. The
trace and span ids are derived from the batch id and the request's ``custom_id``, so
reading the same results twice re-sends identical spans and the server's upsert keeps one
copy. Batch requests are always billed at the batch rate, so a succeeded result is
recorded with ``service_tier = "batch"`` whatever the provider reported; the provider's
own value is kept in ``attributes["usage.service_tier_raw"]``. Errored, canceled and
expired results are not billed: they become error spans without usage. A caller that knows
the provider billed a failed request (an OpenAI Responses body with ``status: "failed"``)
passes its usage, and the error span records it.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from .._serialize import JsonValue
from .._span import Span
from .._tiers import MAX_RAW_TIER_LENGTH
from .._trace_state import TraceState
from ._common import as_int

if TYPE_CHECKING:
    from .._client import Spanlight

logger = logging.getLogger("spanlight")

BATCH_TAG = "spanlight:batch"
SUCCEEDED = "succeeded"


def batch_trace_id(batch_id: str) -> str:
    """The trace id shared by every result of one batch (32 hex characters)."""
    return hashlib.sha256(f"spanlight:batch:{batch_id}".encode()).hexdigest()[:32]


def batch_span_id(batch_id: str, custom_id: str) -> str:
    """The span id of one batch result (16 hex characters)."""
    return hashlib.sha256(f"{batch_id}:{custom_id}".encode()).hexdigest()[:16]


class _RetrievedSpan(Span):
    """A span that starts and ends at the moment its result was read.

    The provider does not report when a batch request ran, so the span claims no
    duration; ``attributes["timing.synthetic"]`` says why.
    """

    def to_payload(self) -> dict[str, JsonValue]:
        payload = super().to_payload()
        payload["end_time"] = payload["start_time"]
        return payload


def record_batch_item(
    client: Spanlight,
    *,
    provider: str,
    batch_id: str,
    custom_id: str,
    result_type: str,
    model: str | None,
    usage: Mapping[str, Any] | None,
    output: Any,
    finish_reason: str | None,
    error: str | None,
    error_message: str | None = None,
) -> bool:
    """Record one batch result as a span. Never raises.

    Returns whether a span was recorded: ``False`` when the client is disabled or the
    result could not be recorded.

    Args:
        client: Spanlight client to report to.
        provider: Provider name, e.g. ``"anthropic"``.
        batch_id: The provider's batch id.
        custom_id: The request's ``custom_id``, unique within the batch.
        result_type: ``"succeeded"`` for a billed result; anything else (``"errored"``,
            ``"canceled"``, ``"expired"``) is recorded as an error, without usage unless
            ``usage`` is given.
        model: Model that served the request, when the result names it.
        usage: Token counts as :meth:`Span.set_usage` keywords (``input_tokens`` is the
            total prompt), plus the provider's raw ``service_tier``.
        output: The response content.
        finish_reason: Why generation stopped, as the provider reports it.
        error: Error class (e.g. ``"invalid_request_error"``); set for a failed request
            even when ``result_type`` is ``"succeeded"``. ``usage``, when given, is still
            recorded on the error span (the provider bills the usage it reports).
        error_message: Human-readable error; defaults to the error class.
    """
    try:
        span = _start_span(client, provider, batch_id, custom_id, model)
        span.set_attributes(
            {
                "batch.id": batch_id,
                "batch.custom_id": custom_id,
                "batch.result_type": result_type,
                "batch.request_available": False,
                "timing.synthetic": True,
            }
        )
        if result_type == SUCCEEDED and error is None:
            _record_success(span, usage, output, finish_reason)
        else:
            error_class = error or result_type
            span.record_error(error_message or error_class)
            span.set_attribute("error.type", error_class)
            if usage is not None:
                # A failed request the provider still reports usage for is billed for it.
                _record_usage(span, usage)
        span.end()
    except Exception:
        logger.debug("Failed to record batch result %r of %r", custom_id, batch_id, exc_info=True)
        return False
    return span.recording


def _start_span(
    client: Spanlight, provider: str, batch_id: str, custom_id: str, model: str | None
) -> Span:
    trace = TraceState(
        trace_id=batch_trace_id(batch_id),
        name=f"batch {batch_id}",
        environment=client.config.environment,
        release=client.config.release,
    )
    trace.update(tags=[BATCH_TAG])
    span = _RetrievedSpan(
        name=f"batch {model}" if model else "batch",
        kind="llm",
        trace=trace,
        parent_span_id=None,
        on_end=client._export_span,
        recording=client.enabled,
        span_id=batch_span_id(batch_id, custom_id),
    )
    span.set_model(model, provider=provider)
    return span


def _record_success(
    span: Span, usage: Mapping[str, Any] | None, output: Any, finish_reason: str | None
) -> None:
    _record_usage(span, usage or {})
    span.set_finish_reason(finish_reason)
    span.set_output(output)


def _record_usage(span: Span, usage: Mapping[str, Any]) -> None:
    span.set_usage(
        input_tokens=as_int(usage.get("input_tokens")),
        output_tokens=as_int(usage.get("output_tokens")),
        cached_tokens=as_int(usage.get("cached_tokens")),
        cache_write_tokens=as_int(usage.get("cache_write_tokens")),
        cache_write_1h_tokens=as_int(usage.get("cache_write_1h_tokens")),
        web_search_requests=as_int(usage.get("web_search_requests")),
        service_tier="batch",
    )
    raw_tier = usage.get("service_tier")
    if isinstance(raw_tier, str) and raw_tier.strip():
        span.set_attribute("usage.service_tier_raw", raw_tier[:MAX_RAW_TIER_LENGTH])
