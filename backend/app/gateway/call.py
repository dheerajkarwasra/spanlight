"""One gateway call in progress, and the `GatewayResult` it answers with.

`Call` holds what `execute`'s checks decided (the fault, a blocking budget, the cache status),
the call's clocks and ids, and builds every kind of answer: a rendered gateway error, a cache
hit, or what the attempt loop got. `_finish` is where each answer is measured (overhead,
metrics) and its span is arranged: written now for a complete answer, or when a stream ends.
"""

import json
from datetime import datetime
from functools import cached_property
from typing import Any

import structlog

from app.core.observability import GATEWAY_OVERHEAD, GATEWAY_REQUESTS
from app.gateway.attempts import LoopResult, RoutePlan, Waits
from app.gateway.budget import BudgetDecision
from app.gateway.cache import CachedResponse, CacheStatus
from app.gateway.cache_key import cache_key as compute_cache_key
from app.gateway.context import (
    UPSTREAM_ERROR_CODE,
    GatewayContext,
    GatewayRequest,
    GatewayResult,
)
from app.gateway.errors import GatewayError, envelope_for, internal, render_error
from app.gateway.faults import AppliedFault
from app.gateway.recorder import credentials_used, record_soon
from app.gateway.response_summary import summarize_response
from app.gateway.routing import resolve_model
from app.gateway.span_losses import span_not_built
from app.gateway.sse import StreamSummary
from app.gateway.stream_forward import GatewayStream
from app.gateway.tracing import build_span, new_span_id, new_trace_id

logger = structlog.get_logger(__name__)


class Call:
    """One call in progress: its clocks, ids and what the checks decided, and its answers."""

    def __init__(self, request: GatewayRequest, context: GatewayContext) -> None:
        self.request, self.context = request, context
        # The route's time budget starts here; overhead, time to first token and the span's
        # start time count from when the call arrived, when the router says (`received`).
        begun = context.monotonic()
        self.now = context.clock()
        self.deadline = begun + context.route.timeout_ms / 1000
        received = request.received
        self.started = received.monotonic if received is not None else begun
        self.started_at: datetime = received.at if received is not None else self.now
        self.waits = Waits(context)
        self.envelope = envelope_for(request.surface, request.client_headers)
        self.trace_id = request.trace.trace_id or new_trace_id()
        self.span_id = new_span_id()
        self.fault: AppliedFault | None = None
        self.budget: BudgetDecision | None = None
        self.cache: CacheStatus = _cache_status(request, context)
        self.loop: LoopResult | None = None  # the attempt loop's result, once the loop has begun

    def remaining_s(self) -> float:
        return max(0.0, self.deadline - self.context.monotonic())

    @cached_property
    def cache_key(self) -> bytes:
        context = self.context
        return compute_cache_key(
            self.request.surface,
            self.request.body,
            route_id=context.route_id,
            route_version=context.route_version,
        )

    def fail(self, error: GatewayError, loop: LoopResult | None = None) -> GatewayResult:
        """A gateway-made answer, rendered in the surface's envelope. Never raises."""
        try:
            return self._fail(error, loop)
        except Exception:
            logger.exception("gateway.render_failed", request_id=self.request.request_id)
            return self._bare_internal_error()

    def abandon(self) -> None:
        """Record a call its caller cancelled before it was answered, as a client disconnect.

        The span lists the attempts made so far, the cancelled one included.
        """
        try:
            result = self._result(self.loop, 499, {})
            result.client_disconnected = True
            result.disconnected_after_ms = (self.context.monotonic() - self.started) * 1000
            self._finish(result, None)
        except Exception:
            logger.exception("gateway.abandon_failed", request_id=self.request.request_id)

    def _fail(self, error: GatewayError, loop: LoopResult | None) -> GatewayResult:
        rendered = render_error(error, self.envelope, self.request.request_id)
        result = self._result(loop, rendered.status, rendered.headers)
        result.body = json.dumps(rendered.body).encode()
        result.headers["content-type"] = "application/json"
        result.error = error
        return self._finish(result, loop)

    def from_cache(self, cached: CachedResponse) -> GatewayResult:
        self.cache = "hit"
        result = self._result(None, 200, {"content-type": cached.content_type})
        result.body, result.model = cached.body, cached.model
        result.original_usage = cached.usage
        summary = summarize_response(self.request.surface, cached.body)
        result.output, result.finish_reason = summary.output, summary.finish_reason
        return self._finish(result, None)

    def from_loop(self, loop: LoopResult, plan: RoutePlan, summary: StreamSummary) -> GatewayResult:
        if loop.status is None or (loop.error is not None and not _passthrough(loop.error)):
            return self.fail(loop.error or internal(self.request.request_id), loop)
        result = self._result(loop, loop.status, dict(loop.headers))
        result.body, result.error = loop.body, loop.error
        if loop.error is not None:
            result.headers["X-Spanlight-Code"] = UPSTREAM_ERROR_CODE
            result.output = _json_or_none(loop.body)
        target = self.context.route.targets[loop.target_index or 0]
        result.model = summary.model or resolve_model(target, plan.model)
        result.usage, result.finish_reason = summary.usage, summary.finish_reason
        result.output = summary.output or result.output
        if _answer_without_final_usage(loop, result.model, summary):
            result.usage_unreported_reason = "no_final_usage"
        if loop.first_byte_at is not None:
            result.ttft_ms = round((loop.first_byte_at - self.started) * 1000, 3)
        return self._finish(result, loop)

    def _result(
        self, loop: LoopResult | None, status: int, headers: dict[str, str]
    ) -> GatewayResult:
        attempts = loop.attempts if loop is not None else []
        headers = {
            **headers,
            "X-Request-ID": self.request.request_id,
            "X-Spanlight-Attempts": str(len(attempts)),
        }
        if self.context.key is not None:
            headers["X-Spanlight-Cache"] = self.cache
        return GatewayResult(
            status=status,
            headers=headers,
            body=None,
            stream=None,
            trace_id=self.trace_id,
            span_id=self.span_id,
            attempts=attempts,
            target_index=loop.target_index if loop is not None else None,
            cache=self.cache,
            fault=self.fault,
            budget=self.budget,
        )

    def _finish(self, result: GatewayResult, loop: LoopResult | None) -> GatewayResult:
        """Measure the overhead, count the call, and arrange for its span to be written."""
        elapsed = self.context.monotonic() - self.started
        upstream = loop.upstream_s if loop is not None else 0.0
        overhead = max(0.0, elapsed - upstream - self.waits.total_s)
        result.overhead_ms = round(overhead * 1000, 3)
        surface = self.request.surface
        GATEWAY_OVERHEAD.labels(surface).observe(overhead)
        GATEWAY_REQUESTS.labels(surface, _outcome(result)).inc()
        if loop is not None and loop.stream is not None and result.error is None:
            result.stream = GatewayStream(
                self.request,
                self.context,
                result,
                loop.stream,
                loop.response,
                started=self.started,
                started_at=self.started_at,
            )
            return result
        try:
            ended_at = self.context.clock()
            span = build_span(self.request, result, self.context, self.started_at, ended_at)
            record_soon(self.context, span, credentials_used(result))
        except Exception as failure:  # noqa: BLE001 - counted and logged, the client was answered
            span_not_built(self.request.request_id, self.context.project_id, failure)
        return result

    def _bare_internal_error(self) -> GatewayResult:
        """The last resort when even rendering failed: a fixed 500 body, no span."""
        request_id = self.request.request_id
        body = {"error": {"message": f"Spanlight gateway error. Request ID {request_id}."}}
        return GatewayResult(
            status=500,
            headers={"content-type": "application/json", "X-Request-ID": request_id},
            body=json.dumps(body).encode(),
            stream=None,
            trace_id=self.trace_id,
            span_id=self.span_id,
            error=internal(request_id),
        )


def _cache_status(request: GatewayRequest, context: GatewayContext) -> CacheStatus:
    if context.cache_ttl_seconds is None:
        return "off"  # no key (an in-process caller) or a key without a TTL
    return "bypass" if request.stream else "miss"


def _passthrough(error: GatewayError) -> bool:
    return error.spanlight_code == UPSTREAM_ERROR_CODE


def _outcome(result: GatewayResult) -> str:
    if result.client_disconnected:
        return "gateway_error"  # cancelled before an answer existed
    if result.budget is not None:
        return "budget_blocked"
    if result.fault is not None:
        return "fault"
    if result.cache == "hit":
        return "cache_hit"
    if result.error is None:
        return "ok"
    return "upstream_error" if _passthrough(result.error) else "gateway_error"


def _json_or_none(body: bytes | None) -> dict[str, Any] | None:
    if body is None:
        return None
    try:
        parsed = json.loads(body)
    except (ValueError, RecursionError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _answer_without_final_usage(
    loop: LoopResult, model: str | None, summary: StreamSummary
) -> bool:
    """A complete (non-streamed) success whose output count is unknown: its cost is unknown too.

    A stream's usage is read later, as it is forwarded (`stream_forward._take_summary`); an
    error answer reports no usage because nothing was generated, which is not a lost count.
    """
    return (
        loop.succeeded
        and loop.stream is None
        and model is not None
        and (summary.usage is None or not summary.usage_complete)
    )
