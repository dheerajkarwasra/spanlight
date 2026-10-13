"""The one `llm` span a gateway call becomes. Pure: no database, no clock, no I/O.

`build_span` turns a finished call into a dict in the native ingestion shape (`SpanIn`), which
`recorder.record` writes through `ingest_spans` like any SDK span. The span joins the caller's
trace when the request carried valid `x-spanlight-*` ids, and starts a new one otherwise.

The client's body is parsed leniently (it is forwarded as sent), so it may hold a NUL, a lone
surrogate or a NaN, none of which Postgres stores. The span is made storable here
(`storable_json`), the one place every gateway span is built, so neither the background
recorder nor a `DirectSpans` caller loses it to a refused write.
"""

import secrets
from datetime import datetime
from typing import Any

from app.core.text import storable_json
from app.db.models import FaultScenario, ProviderCredential
from app.gateway.context import (
    UPSTREAM_ERROR_CODE,
    GatewayContext,
    GatewayRequest,
    GatewayResult,
)
from app.gateway.faults import BEFORE_SCENARIOS, fault_code, lab_tag
from app.gateway.sse import Usage
from app.gateway.trace_headers import MAX_TAGS
from app.ingest.request_hash import request_hash

MAX_NAME_LENGTH = 256
MAX_STATUS_MESSAGE = 4096
# Match no error-class pattern, so with their 200 status these spans' class is `unknown`.
MALFORMED_JSON_MESSAGE = "the Integration Lab cut the response body short on purpose"
TRUNCATED_STREAM_MESSAGE = "the Integration Lab ended the stream before its final event on purpose"


def new_trace_id() -> str:
    return _nonzero_hex(16)


def new_span_id() -> str:
    return _nonzero_hex(8)


def _nonzero_hex(size: int) -> str:
    while True:
        value = secrets.token_hex(size)
        if value.strip("0"):
            return value


def build_span(
    request: GatewayRequest,
    result: GatewayResult,
    context: GatewayContext,
    started_at: datetime,
    ended_at: datetime,
) -> dict[str, Any]:
    """The call's span as a `SpanIn`-shaped dict that Postgres can store."""
    model = result.model or _requested_model(request)
    credential = _credential(result, context)
    message = status_message(result)
    failed = message is not None
    span = {
        "trace_id": result.trace_id,
        "span_id": result.span_id,
        "parent_span_id": request.trace.parent_span_id,
        "name": f"{request.surface} {model}"[:MAX_NAME_LENGTH],
        "kind": "llm",
        "status": "error" if failed else "ok",
        "status_message": message,
        "start_time": started_at.isoformat(),
        "end_time": max(ended_at, started_at).isoformat(),
        "provider": credential.provider.value if credential is not None else None,
        "model": model[:MAX_NAME_LENGTH] if model else None,
        "usage": _usage(result),
        "time_to_first_token_ms": result.ttft_ms if request.stream else None,
        "input": request.body,
        "output": result.output,
        "attributes": _attributes(request, result, context),
        # Computed here, from what the client sent: ingestion drops `input` when the project
        # does not capture payloads, and the requested model keeps the hash stable across a
        # fallback to another target.
        "request_hash": request_hash(_requested_model(request), request.body),
        "trace": _trace(request, result, context),
    }
    storable: dict[str, Any] = storable_json(span)
    return storable


def _trace(
    request: GatewayRequest, result: GatewayResult, context: GatewayContext
) -> dict[str, Any]:
    return {
        "environment": context.environment or None,
        "release": request.trace.release,
        "user_id": request.trace.user_id,
        "session_id": request.trace.session_id,
        "tags": trace_tags(request, result, context),
    }


def status_message(result: GatewayResult) -> str | None:
    """Why the call failed, or None when it did not.

    `"<status> <spanlight_code or the provider's error type>: <message>"` for errors, the
    disconnect time when the client went away, the reason when a stream broke off.
    """
    if result.client_disconnected:
        after = round(result.disconnected_after_ms or 0)
        return f"client disconnected after {after} ms"
    error = result.error
    if error is not None:
        code = error.spanlight_code
        if code == UPSTREAM_ERROR_CODE and error.openai is not None:
            code = error.openai.type  # the provider's own error type
        return f"{error.status} {code}: {error.message}"[:MAX_STATUS_MESSAGE]
    if result.stream_error is not None:
        return f"{result.status} stream_error: {result.stream_error}"[:MAX_STATUS_MESSAGE]
    fault = result.fault
    cut = _cut_by_fault(result)
    if fault is not None and cut is not None:
        return f"{result.status} {fault_code(fault.scenario)}: {cut}"
    return None


def _cut_by_fault(result: GatewayResult) -> str | None:
    """Why a lab fault cut an otherwise successful answer short, or None when none did.

    `malformed_json` always cuts the body; `truncated_stream` cuts a stream that had more than
    its `after_chunks` frames (`cut_by_fault`). The client got a success status with an answer
    it cannot trust, so the span is a failure: what the client does next is then judged like
    after any other failure.
    """
    fault = result.fault
    if fault is None or result.status >= 400:
        return None
    if fault.scenario == FaultScenario.MALFORMED_JSON:
        return MALFORMED_JSON_MESSAGE
    if fault.scenario == FaultScenario.TRUNCATED_STREAM and result.cut_by_fault:
        return TRUNCATED_STREAM_MESSAGE
    return None


def trace_tags(
    request: GatewayRequest, result: GatewayResult, context: GatewayContext
) -> list[str]:
    """The Lab tag, then the key's default tags, then the header tags; at most 20, no repeats."""
    ordered = [
        *([lab_tag(result.fault.scenario)] if result.fault is not None else []),
        *context.default_tags,
        *request.trace.tags,
    ]
    return list(dict.fromkeys(ordered))[:MAX_TAGS]


def _attributes(
    request: GatewayRequest, result: GatewayResult, context: GatewayContext
) -> dict[str, Any]:
    credential = _credential(result, context)
    upstream_status, retry_after = _upstream_status(result)
    targets_tried = list(dict.fromkeys(attempt.target_index for attempt in result.attempts))
    attributes: dict[str, Any] = {
        "spanlight.gateway.key_id": str(context.key_id) if context.key_id else None,
        "spanlight.gateway.request_id": request.request_id,
        "spanlight.gateway.surface": request.surface,
        "spanlight.gateway.route": context.route_name,
        "spanlight.gateway.route_version": context.route_version,
        "spanlight.gateway.target": credential.name if credential is not None else None,
        "spanlight.gateway.provider": credential.provider.value if credential else None,
        "spanlight.gateway.attempts": len(result.attempts),
        "spanlight.gateway.fallbacks": max(0, len(targets_tried) - 1),
        "spanlight.gateway.overhead_ms": result.overhead_ms,
        "spanlight.gateway.stream": request.stream,
        "spanlight.gateway.upstream_status": upstream_status,
        "spanlight.gateway.retry_after_s": retry_after,
        "spanlight.gateway.client_disconnected": result.client_disconnected,
        "spanlight.cache": result.cache,
        "finish_reason": result.finish_reason,
    }
    if result.cache == "hit" and result.original_usage is not None:
        attributes["spanlight.cache.original_usage"] = _usage_dict(result.original_usage)
    if result.fault is not None:
        attributes["spanlight.fault.scenario"] = result.fault.scenario.value
        attributes["spanlight.fault.profile_id"] = str(result.fault.profile_id)
    if result.budget is not None:
        attributes["spanlight.budget.blocked"] = True
        budget_id = result.budget.budget_id
        attributes["spanlight.budget.id"] = str(budget_id) if budget_id is not None else None
    return attributes


def _upstream_status(result: GatewayResult) -> tuple[int | None, float | None]:
    """The provider's status and `Retry-After`: the last attempt's, or a fault's simulated ones.

    A fault answered before the upstream call makes no attempt, so its span carries the status
    (and, for `rate_limited`, the `Retry-After`) the gateway answered with instead. Its
    `spanlight.gateway.attempts` stays 0, which tells a simulated status from a real one.
    """
    if result.attempts:
        retry_after = next(
            (a.retry_after for a in reversed(result.attempts) if a.retry_after is not None),
            None,
        )
        return result.attempts[-1].status, retry_after
    fault, error = result.fault, result.error
    if (
        fault is not None
        and fault.scenario in BEFORE_SCENARIOS
        and error is not None
        and error.spanlight_code == fault_code(fault.scenario)
    ):
        return error.status, error.retry_after
    return None, None


def _usage(result: GatewayResult) -> dict[str, Any] | None:
    if result.cache == "hit":
        # Nothing was billed: the answer came from the cache.
        return {"input_tokens": 0, "output_tokens": 0, "cached_tokens": 0}
    return _usage_dict(result.usage) if result.usage is not None else None


def _usage_dict(usage: Usage) -> dict[str, Any]:
    return {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cached_tokens": usage.cached_tokens,
    }


def _credential(result: GatewayResult, context: GatewayContext) -> ProviderCredential | None:
    if result.target_index is None:
        return None
    target = context.route.targets[result.target_index]
    return context.credentials.get(target.credential_id)


def _requested_model(request: GatewayRequest) -> str | None:
    model = request.body.get("model")
    return model if isinstance(model, str) else None
