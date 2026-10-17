"""Forwarding a provider's stream to the client, then writing the call's span.

`forward_stream` yields the upstream bytes unchanged while the surface's observer reads copies
(usage, model, finish reason, the reassembled output). The one exception is the usage-only chunk
of a chat completion stream when the gateway, not the client, asked for it: the observer reads
it and the client never sees it (`app.gateway.usage_chunk`).

The span is written once the stream ends, whichever way it ends (`GatewayStream` also covers a
stream nobody started reading):

- the provider finished: a normal span;
- the provider went quiet for the idle timeout or its connection broke: an error span with the
  reason, and the client's stream simply ends;
- a lab `truncated_stream` fault withheld the rest (`StreamCutByFault`): the client's stream
  ends cleanly and the span is an error naming the fault;
- the client went away (the generator is closed or cancelled): an error span saying after how
  long, with `spanlight.gateway.client_disconnected` true.

The span is written by a background task, so it survives the cancellation of the request.
"""

import asyncio
import contextlib
from collections.abc import AsyncGenerator, AsyncIterator
from datetime import datetime
from typing import cast

import httpx
import structlog

from app.gateway.adapters import UpstreamResponse
from app.gateway.context import GatewayContext, GatewayRequest, GatewayResult
from app.gateway.errors import Surface
from app.gateway.fault_apply import StreamCutByFault
from app.gateway.recorder import credentials_used, record_soon
from app.gateway.responses_observer import OpenAiResponsesObserver
from app.gateway.span_losses import span_not_built
from app.gateway.sse import StreamObserver
from app.gateway.stream_observers import AnthropicMessagesObserver, OpenAiChatObserver
from app.gateway.streaming import observe_stream
from app.gateway.tracing import build_span
from app.gateway.upstream_io import STREAM_IDLE_TIMEOUT_S, close_quietly
from app.gateway.usage_chunk import UsageChunkFilter, injects_usage

logger = structlog.get_logger(__name__)


def observer_for(surface: Surface) -> StreamObserver:
    if surface == "messages":
        return AnthropicMessagesObserver()
    if surface == "responses":
        return OpenAiResponsesObserver()
    return OpenAiChatObserver()


class GatewayStream:
    """The client's stream (`ByteStream`). Read it to the end or `aclose()` it.

    Either way the span is written exactly once. Closing a stream nobody started reading closes
    the provider response, which an unstarted generator could not do, and records the call as a
    client disconnect.
    """

    def __init__(
        self,
        request: GatewayRequest,
        context: GatewayContext,
        result: GatewayResult,
        upstream: AsyncIterator[bytes],
        response: UpstreamResponse | None,
        *,
        started: float,
        started_at: datetime,
    ) -> None:
        self._request, self._context, self._result = request, context, result
        self._response = response
        self._started, self._started_at = started, started_at
        self._forwarding = _forward(
            request, context, result, upstream, started=started, started_at=started_at
        )
        self._state = "new"  # new, open (iteration began) or closed

    def __aiter__(self) -> "GatewayStream":
        return self

    async def __anext__(self) -> bytes:
        if self._state == "closed":
            raise StopAsyncIteration
        self._state = "open"
        return await self._forwarding.__anext__()

    async def aclose(self) -> None:
        if self._state == "closed":
            return
        began, self._state = self._state == "open", "closed"
        if began:
            await self._forwarding.aclose()  # its `finally` writes the span
            return
        result = self._result
        result.client_disconnected = True
        result.disconnected_after_ms = (self._context.monotonic() - self._started) * 1000
        if result.model is not None:  # the provider answered; its usage was never read
            result.usage_unreported_reason = _unreported_reason(result)
        _record(self._request, self._context, result, self._started_at)
        await close_quietly(self._response)


async def _forward(
    request: GatewayRequest,
    context: GatewayContext,
    result: GatewayResult,
    upstream: AsyncIterator[bytes],
    *,
    started: float,
    started_at: datetime,
) -> AsyncGenerator[bytes, None]:
    """The upstream bytes through the observer; the span is written when this ends."""
    observer = observer_for(request.surface)
    # The usage chunk the gateway asked for itself is read by the observer, not forwarded.
    hidden = UsageChunkFilter() if injects_usage(request.surface, True, request.body) else None
    try:
        # `observe_stream` is an async generator; closing it closes the upstream response.
        observing = cast(AsyncGenerator[bytes, None], observe_stream(upstream, observer, _ignore))
        async with contextlib.aclosing(observing) as observed:
            try:
                async for chunk in observed:
                    forwarded = hidden.feed(chunk) if hidden is not None else chunk
                    if forwarded:
                        yield forwarded
            except StreamCutByFault:
                result.cut_by_fault = True  # the client's stream ends here, cleanly
        if hidden is not None and (rest := hidden.flush()):
            yield rest
    except TimeoutError:
        result.stream_error = f"the provider sent nothing for {STREAM_IDLE_TIMEOUT_S:g} s"
    except httpx.TransportError as error:
        result.stream_error = f"the provider connection failed ({type(error).__name__})"
    except (GeneratorExit, asyncio.CancelledError):
        result.client_disconnected = True
        result.disconnected_after_ms = (context.monotonic() - started) * 1000
        raise
    except Exception:
        logger.exception("gateway.stream_failed", request_id=request.request_id)
        result.stream_error = "the gateway failed while forwarding the stream"
    finally:
        _take_summary(result, observer)
        _record(request, context, result, started_at)


def _take_summary(result: GatewayResult, observer: StreamObserver) -> None:
    """What the observer read: the model, usage, finish reason and reassembled output.

    A stream that ended before the provider's final usage keeps what it had (the input count
    from `message_start`, say) and says why the rest is unknown, so the span has no cost
    rather than a cheap one.
    """
    summary = observer.summary()
    result.model = summary.model or result.model
    result.usage = summary.usage
    result.finish_reason = summary.finish_reason
    result.output = summary.output
    if result.model is not None and (summary.usage is None or not summary.usage_complete):
        result.usage_unreported_reason = _unreported_reason(result)


def _unreported_reason(result: GatewayResult) -> str:
    """`usage.unreported_reason` for a stream without its final usage, by the first cause."""
    if result.client_disconnected:
        return "client_disconnected"
    if result.stream_error is not None:
        return "stream_error"
    if result.cut_by_fault:
        return "stream_cut_by_fault"
    return "no_final_usage"


def _record(
    request: GatewayRequest, context: GatewayContext, result: GatewayResult, started_at: datetime
) -> None:
    """Queue the span; a failure to build it is counted and logged, never raised into the stream."""
    try:
        span = build_span(request, result, context, started_at, context.clock())
        record_soon(context, span, credentials_used(result))
    except Exception as failure:  # noqa: BLE001 - counted and logged, the client has the stream
        span_not_built(request.request_id, context.project_id, failure)


def _ignore() -> None:
    """The time to first byte was taken when the attempt loop received it."""
