"""Injecting a decided fault (`app.gateway.faults`) into one gateway call.

Two entry points, matching where a scenario acts:

- `apply_before` runs instead of the provider call. It returns the provider-shaped
  `GatewayError` to answer with (after holding the call, for `timeout`), or `None` when the
  scenario acts around the call instead.
- `apply_around` wraps the provider's real response. It returns a response whose body is cut
  (`malformed_json`), stopped early (`truncated_stream`) or delayed (`slow_response`); any
  other scenario returns the response unchanged. A stream stopped early raises
  `StreamCutByFault` after its last forwarded byte, so the forwarder can end the client's stream
  cleanly and still record the call as cut.

Fault errors copy each provider's wording, so client code is exercised against realistic
bodies, and carry `spanlight_code` / `X-Spanlight-Code` `FAULT_<SCENARIO>` plus the
`X-Spanlight-Fault: <scenario>` header. Waiting goes through the injected `sleep`, so tests run
on a fake clock.
"""

import dataclasses
from collections.abc import AsyncIterator, Awaitable, Callable

from app.gateway.adapters.base import UpstreamResponse
from app.gateway.errors import Envelope, ErrorKind, GatewayError
from app.gateway.fault_params import (
    AuthExpiredParams,
    MalformedJsonParams,
    Provider5xxParams,
    RateLimitedParams,
    ScopeDeniedParams,
    SlowResponseParams,
    TimeoutParams,
    TruncatedStreamParams,
    UnsupportedParameterParams,
)
from app.gateway.faults import FAULT_HEADER, AppliedFault, fault_code
from app.gateway.sse import FrameTooLarge, SseState, parse_frames

Sleep = Callable[[float], Awaitable[None]]

MAX_BUFFERED_BYTES = 10 * 1024 * 1024
"""The most of a response body `malformed_json` reads before cutting it (10 MB)."""

_SERVER_ERROR = "The server had an error while processing your request."


class StreamCutByFault(Exception):  # noqa: N818 - a signal, not an error
    """A `truncated_stream` fault withheld the rest of the stream (raised after its last byte)."""


# Status and both providers' wording, for the scenarios whose error takes no parameters.
_FIXED: dict[type[object], tuple[int, ErrorKind, ErrorKind]] = {
    AuthExpiredParams: (
        401,
        ErrorKind("invalid_request_error", "invalid_api_key", "Incorrect API key provided."),
        ErrorKind("authentication_error", message="invalid x-api-key"),
    ),
    ScopeDeniedParams: (
        403,
        ErrorKind(
            "invalid_request_error",
            "insufficient_permissions",
            "You have insufficient permissions for this operation.",
        ),
        ErrorKind(
            "permission_error",
            message="Your API key does not have permission to use the specified resource.",
        ),
    ),
    RateLimitedParams: (
        429,
        ErrorKind("requests", "rate_limit_exceeded", "Rate limit reached for requests."),
        ErrorKind("rate_limit_error", message="Number of requests has exceeded your rate limit."),
    ),
}


async def apply_before(
    fault: AppliedFault,
    envelope: Envelope,
    *,
    remaining_s: float,
    sleep: Sleep,
    route_timeout_ms: int | None = None,
) -> GatewayError | None:
    """The error that replaces the provider call, or `None` for a scenario applied around it.

    The error carries both providers' wording, so it renders correctly in either envelope;
    `envelope` is the one the caller renders it in. `timeout` holds for `hold_ms` or what is
    left of the route's budget (`remaining_s`), whichever is shorter, then answers like the
    gateway's own `UPSTREAM_TIMEOUT`, naming `route_timeout_ms` (the held time when unset).
    """
    del envelope  # Both envelopes' kinds are set; kept so call sites state what they render.
    params = fault.params
    retry_after: float | None = None
    param: str | None = None
    fixed = _FIXED.get(type(params))
    if fixed is not None:
        status, openai, anthropic = fixed
        if isinstance(params, RateLimitedParams):
            retry_after = params.retry_after_s
    elif isinstance(params, UnsupportedParameterParams):
        param = params.param
        status = 400
        openai = ErrorKind(
            "invalid_request_error",
            "unsupported_parameter",
            f"Unsupported parameter: '{param}' is not supported with this model.",
        )
        anthropic = ErrorKind(
            "invalid_request_error", message=f"{param}: Extra inputs are not permitted"
        )
    elif isinstance(params, Provider5xxParams):
        status = params.status
        openai = ErrorKind("server_error", message=_SERVER_ERROR)
        anthropic = (
            ErrorKind("overloaded_error", message="Overloaded")
            if status == 529
            else ErrorKind("api_error", message="Internal server error")
        )
    elif isinstance(params, TimeoutParams):
        hold_s = min(params.hold_ms / 1000, max(0.0, remaining_s))
        if hold_s > 0:
            await sleep(hold_s)
        budget_ms = route_timeout_ms if route_timeout_ms is not None else round(hold_s * 1000)
        message = f"The provider did not respond within the route's {budget_ms} ms budget."
        status = 504
        openai = ErrorKind("server_error", message=message)
        anthropic = ErrorKind("timeout_error", message=message)
    else:
        return None
    return GatewayError(
        status,
        fault_code(fault.scenario),
        openai.message or "",
        retry_after=retry_after,
        param=param,
        openai=openai,
        anthropic=anthropic,
        extra_headers=((FAULT_HEADER, fault.scenario.value),),
    )


async def apply_around(
    fault: AppliedFault,
    upstream: UpstreamResponse,
    *,
    sleep: Sleep,
    remaining_s: float | None = None,
) -> UpstreamResponse:
    """`upstream` with the fault applied to its body and `X-Spanlight-Fault` added.

    Status and the other headers (content type included) are kept, and `aclose()` on the
    result still closes the real upstream response. `slow_response`'s delay is capped by
    `remaining_s` when given. `malformed_json` reads the body here (up to
    `MAX_BUFFERED_BYTES`; a longer body is cut from that much); the other scenarios act while
    the caller iterates the body. Upstream read errors propagate unchanged.
    """
    params = fault.params
    body: AsyncIterator[bytes]
    if isinstance(params, MalformedJsonParams):
        full = await _read_capped(upstream)
        body = _single(full[: int(len(full) * params.keep_fraction)])
    elif isinstance(params, TruncatedStreamParams):
        body = _truncated(upstream, params.after_chunks)
    elif isinstance(params, SlowResponseParams):
        delay_s = params.delay_ms / 1000
        if remaining_s is not None:
            delay_s = min(delay_s, max(0.0, remaining_s))
        body = _delayed(upstream, delay_s, sleep)
    else:
        return upstream
    headers = {**upstream.headers, FAULT_HEADER: fault.scenario.value}
    return dataclasses.replace(upstream, headers=headers, body=body)


async def _read_capped(upstream: UpstreamResponse) -> bytes:
    buffer = bytearray()
    try:
        async for chunk in upstream.body:
            buffer += chunk
            if len(buffer) >= MAX_BUFFERED_BYTES:
                break
    finally:
        await upstream.aclose()
    return bytes(buffer[:MAX_BUFFERED_BYTES])


async def _single(data: bytes) -> AsyncIterator[bytes]:
    if data:
        yield data


async def _truncated(upstream: UpstreamResponse, after_chunks: int) -> AsyncIterator[bytes]:
    """The upstream bytes up to the end of frame `after_chunks`, byte for byte, then nothing.

    Frames are counted with the gateway's SSE parser (comment-only blocks are not frames), so
    the end marker (`[DONE]`, `message_stop`) is never sent; a stream with no more than
    `after_chunks` frames passes whole. A frame over the parser's limit ends the stream. When
    bytes were withheld, `StreamCutByFault` is raised once the last forwarded piece is taken.
    """
    state = SseState()
    seen = 0
    recent = b""  # the last bytes forwarded, to finish a CRLF frame end cut between chunks
    try:
        async for chunk in upstream.body:
            before = SseState(bytearray(state.buffer), state.scanned)
            try:
                completed = len(parse_frames(chunk, state))
                if seen + completed < after_chunks:
                    seen += completed
                    recent = (recent + chunk)[-3:]
                    yield chunk
                    continue
                piece = chunk[: _end_of_frame(chunk, before, after_chunks - seen)]
            except FrameTooLarge:
                return
            # The parser ends a CRLF-terminated frame at `\r\n\r`; send the `\n` that
            # completes `\r\n\r\n`, which is the upstream's next byte in a CRLF stream.
            if (recent + piece).endswith(b"\r\n\r"):
                piece += b"\n"
            if piece:
                yield piece
            if chunk[len(piece) :] or await anext(upstream.body, None) is not None:
                raise StreamCutByFault
            return
    finally:
        await upstream.aclose()


def _end_of_frame(chunk: bytes, state: SseState, needed: int) -> int:
    """The offset in `chunk` just past the `needed`-th frame that completes in it."""
    completed = 0
    for index in range(len(chunk)):
        completed += len(parse_frames(chunk[index : index + 1], state))
        if completed >= needed:
            return index + 1
    return len(chunk)


async def _delayed(
    upstream: UpstreamResponse, delay_s: float, sleep: Sleep
) -> AsyncIterator[bytes]:
    """The upstream bytes unchanged, with the first one held back by `delay_s`."""
    waited = False
    try:
        async for chunk in upstream.body:
            if not waited:
                waited = True
                await sleep(delay_s)
            yield chunk
        if not waited:
            await sleep(delay_s)
    finally:
        await upstream.aclose()


__all__ = ["MAX_BUFFERED_BYTES", "Sleep", "StreamCutByFault", "apply_around", "apply_before"]
