"""The span of a call the `/gw/v1` router refused for a rate limit, before `execute` ran.

`check_limits` answers `RATE_LIMITED` (the organization ceiling, the key's requests or its
tokens per minute) once the key is authenticated and before the body is read. The call still
happened, and a platform owner must be able to tell the gateway key's own 429s from the
provider's, so it leaves a span like any other gateway-made error: class
`rate_limit`, kind `gateway`, under the key's project, with the key as `source_key_id`.

Refusals are sampled (`app.gateway.refusal_sampler`): at most one span per gateway key per
second, and the next span a key gets carries `spanlight.gateway.refusals_suppressed`, the
refusals of that key left without a span since its previous one. Each refusal span starts a
trace of its own, whatever trace the client pinned (`x-spanlight-trace-id` and its parent are
ignored), so a flood can neither grow one trace nor make every write re-sum it. A refusal
flood therefore costs the database about one write per key per second.

Everything comes from values already in memory, and the span is queued on the recorder's bounded
backlog (`record_soon`) at low priority, so a refusal gains no database round trip and never
waits, and a refusal flood cannot crowd out the spans of admitted calls: once half the backlog
is taken a refusal's span is dropped as an `overflow` loss. Its write does not count the call in
the key's minute (`span_write`): it was not served. The span records the `Retry-After` the client
was told as `spanlight.gateway.retry_after_s`, as a lab fault's simulated 429 does. The body was
never read, so the model, the input, its request hash and whether the call streams are unknown
(null), and no provider answered, so the span names no target. The client's answer is rendered
by the router's error handler exactly as before; nothing here touches it, and nothing here
raises.
"""

import dataclasses
from datetime import datetime

from app.gateway.context import GatewayContext, GatewayRequest, GatewayResult
from app.gateway.errors import GatewayError
from app.gateway.recorder import record_soon
from app.gateway.refusal_sampler import RefusalSampler
from app.gateway.span_losses import span_not_built
from app.gateway.tracing import build_span, new_span_id, new_trace_id

_sampler = RefusalSampler()


def refusal_sampler() -> RefusalSampler:
    """This process's refusal sampler."""
    return _sampler


# Attributes `build_span` derives from a read body or a consulted cache; neither exists here.
_UNKNOWN_ATTRIBUTES = ("spanlight.gateway.stream", "spanlight.cache")


def record_refusal(request: GatewayRequest, context: GatewayContext, error: GatewayError) -> None:
    """Queue the span of `request`, refused with `error` before its body was read. Never raises.

    `request` carries the surface, request id, trace headers and arrival time; its body is
    empty because it was never read.
    """
    try:
        suppressed = _sampler.admit(context.key_id or context.project_id, context.monotonic())
        if suppressed is None:
            return
        request = _own_trace(request)
        ended_at = context.clock()
        received = request.received
        started_at: datetime = received.at if received is not None else ended_at
        result = GatewayResult(
            status=error.status,
            headers={},  # the rendered error's headers are the gateway's, never the provider's
            body=None,
            stream=None,
            trace_id=new_trace_id(),
            span_id=new_span_id(),
            error=error,
            overhead_ms=_overhead_ms(request, context),
        )
        span = build_span(request, result, context, started_at, ended_at)
        span.update(input=None, request_hash=None)
        for name in _UNKNOWN_ATTRIBUTES:
            span["attributes"][name] = None
        span["attributes"]["spanlight.gateway.retry_after_s"] = error.retry_after
        span["attributes"]["spanlight.gateway.refusals_suppressed"] = suppressed
        record_soon(context, span, low_priority=True)
    except Exception as failure:  # noqa: BLE001 - counted and logged, the client is answered anyway
        span_not_built(request.request_id, context.project_id, failure)


def _own_trace(request: GatewayRequest) -> GatewayRequest:
    """`request` without the client's trace and parent ids: a refusal starts a trace of its own,
    since a pinned trace would grow with every refusal of a flood."""
    trace = dataclasses.replace(request.trace, trace_id=None, parent_span_id=None)
    return dataclasses.replace(request, trace=trace)


def _overhead_ms(request: GatewayRequest, context: GatewayContext) -> float | None:
    """The time the gateway spent on the call so far: authentication and the limits."""
    if request.received is None:
        return None
    return round(max(0.0, context.monotonic() - request.received.monotonic) * 1000, 3)
