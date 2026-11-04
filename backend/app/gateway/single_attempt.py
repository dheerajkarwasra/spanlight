"""One upstream attempt: send the request, read what comes back, name how it ended.

How an attempt can end, and what the client gets when it is the last one:

- a 2xx response: the answer (with an around fault applied, when one was decided);
- a 4xx or 5xx response: that response, byte for byte;
- a 3xx response: `UPSTREAM_UNREACHABLE` (redirect variant), never retried;
- a timeout: `UPSTREAM_TIMEOUT`; a connection error: `UPSTREAM_UNREACHABLE`;
- a private or local address, or `http://` where it is not allowed: `UPSTREAM_BLOCKED`, never
  retried or fallen back from (the configuration is wrong, not the provider);
- an answer over `MAX_RESPONSE_BYTES`: `UPSTREAM_UNREACHABLE`, never retried.

A non-streaming answer is read whole here, and a stream's first chunk is awaited here, both
within what is left of the route's budget. Whatever else interrupts the attempt (a cancellation,
an unexpected error) closes the provider response before it propagates, so no pooled
connection is left open.
"""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from app.gateway import upstream_io
from app.gateway.adapters import ProviderAdapter, UpstreamRequest, UpstreamResponse
from app.gateway.context import GatewayContext, GatewayRequest
from app.gateway.errors import GatewayError, upstream_redirect
from app.gateway.fault_apply import Sleep, apply_around
from app.gateway.faults import AppliedFault
from app.gateway.routing import AttemptOutcome, attempt_timeout
from app.gateway.upstream_io import (
    FailureKind,
    classify_failure,
    close_quietly,
    idle_bounded,
    parse_retry_after,
    read_all,
    upstream_failure,
)


@dataclass
class AttemptEnd:
    """One attempt's ending: a response (with its body or stream) or a failure kind."""

    outcome: AttemptOutcome | None = None  # None when the attempt succeeded
    status: int | None = None
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes | None = None
    stream: AsyncIterator[bytes] | None = None
    response: UpstreamResponse | None = None  # a stream's provider response, still open
    error: GatewayError | None = None  # the client's answer if this attempt is the last
    stream_deadline: float | None = None
    label: str | None = None  # names the failure in `Attempt.error` and the metric, if not the kind


async def attempt_once(
    request: GatewayRequest,
    context: GatewayContext,
    adapter: ProviderAdapter,
    upstream: UpstreamRequest,
    fault: AppliedFault | None,
    *,
    deadline: float,
    sleep: Sleep,
) -> AttemptEnd:
    """Send `upstream` and read the answer before `deadline` (a `context.monotonic()` time)."""
    remaining = deadline - context.monotonic()
    response: UpstreamResponse | None = None
    try:
        async with asyncio.timeout(remaining):
            response = await adapter.send(context.http, upstream, attempt_timeout(remaining))
            return await _read(request, context, response, fault, deadline, sleep)
    except BaseException as error:
        # Cancelled, failed or unexpected: the provider response is closed before anything else.
        await close_quietly(response)
        failure = classify_failure(error, context.route.timeout_ms)
        if failure is None:
            raise
        ended = _failed(failure.kind, failure.error)
        ended.label = failure.label
        return ended


async def _read(
    request: GatewayRequest,
    context: GatewayContext,
    response: UpstreamResponse,
    fault: AppliedFault | None,
    deadline: float,
    sleep: Sleep,
) -> AttemptEnd:
    status, headers = response.status, dict(response.headers)
    if 300 <= status < 400:
        await response.aclose()
        return AttemptEnd(
            AttemptOutcome(status, "status"), status, headers, error=upstream_redirect()
        )
    if not 200 <= status < 300:
        body = await read_all(response)
        retry_after = parse_retry_after(response.headers)
        outcome = AttemptOutcome(status, "status", retry_after)
        return AttemptEnd(outcome, status, headers, body, error=upstream_failure(status, body))
    answer = response
    if fault is not None:
        answer = await apply_around(
            fault, response, sleep=sleep, remaining_s=deadline - context.monotonic()
        )
        headers = dict(answer.headers)
    if not request.stream:
        return AttemptEnd(None, status, headers, await read_all(answer))
    iterator = aiter(answer.body)
    first = await anext(iterator, None)
    stream_deadline = asyncio.get_running_loop().time() + upstream_io.STREAM_TOTAL_TIMEOUT_S
    stream = idle_bounded(first, iterator, deadline=stream_deadline)
    return AttemptEnd(
        None, status, headers, stream=stream, response=answer, stream_deadline=stream_deadline
    )


def _failed(kind: FailureKind, error: GatewayError | None) -> AttemptEnd:
    return AttemptEnd(AttemptOutcome(None, kind), None, error=error)
