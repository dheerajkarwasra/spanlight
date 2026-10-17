"""Reading what a provider sent back, and describing its failures. No routing decisions here.

`read_all` reads a complete answer (at most `MAX_RESPONSE_BYTES`), `idle_bounded` forwards a
stream with the idle timeout between chunks, `parse_retry_after` reads the provider's wait,
`upstream_failure` / `insecure_url` are the errors the attempt loop reports, and
`classify_failure` names how a send or read that raised ended, for model calls and `/models`
alike.
"""

import asyncio
import contextlib
import dataclasses
import math
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Literal

import httpx

from app.core.egress import BlockedAddress, InsecureUrl
from app.gateway.adapters import UpstreamResponse
from app.gateway.context import UPSTREAM_ERROR_CODE
from app.gateway.errors import ErrorKind, GatewayError, upstream_blocked, upstream_timeout
from app.gateway.fault_apply import MAX_BUFFERED_BYTES
from app.gateway.response_summary import upstream_error_detail

STREAM_IDLE_TIMEOUT_S = 60.0
MAX_RESPONSE_BYTES = MAX_BUFFERED_BYTES
"""The largest non-streaming answer the gateway reads (10 MB); a longer one fails the attempt."""

UPSTREAM_ERROR_TYPE = "upstream_error"
"""The status message's stand-in for a provider error whose body names no error type. It is
Spanlight's word, not the provider's, so the span's `provider.error.type` is left unset."""

_INSECURE_URL_MESSAGE = "The provider URL must use https:// on this server."
_TOO_LARGE_MESSAGE = "The provider's answer exceeds the gateway's 10 MB limit."

FailureKind = Literal["status", "timeout", "connection_error", "blocked"]


class ResponseTooLarge(Exception):  # noqa: N818 - reads as the condition, like the egress errors
    """The provider's answer is longer than `MAX_RESPONSE_BYTES`."""


def upstream_failure(status: int, body: bytes) -> GatewayError:
    """A provider's own error, described for the span (the client gets the provider's bytes).

    Both envelope kinds carry the provider's error type, which the span's status message names,
    and its code, which the span records. Neither is rendered: the client gets the provider's
    bytes, so the code changes nothing the client sees.
    """
    detail = upstream_error_detail(body)
    message = detail.message or f"The provider answered with HTTP {status}."
    error_kind = ErrorKind(detail.type or UPSTREAM_ERROR_TYPE, code=detail.code, message=message)
    return GatewayError(
        status, UPSTREAM_ERROR_CODE, message, openai=error_kind, anthropic=error_kind
    )


@dataclass(frozen=True)
class UpstreamFailure:
    """How a send or read that raised ended. `error` is None for a connection error: the caller
    says how many attempts failed. `label` names the failure where the kind is not enough."""

    kind: FailureKind
    error: GatewayError | None
    label: str | None = None


def classify_failure(error: BaseException, timeout_ms: int) -> UpstreamFailure | None:
    """The upstream failure `error` stands for, or None when it is not one (re-raise it).

    A private address or a refused `http://` is `blocked` (never retried: the configuration is
    wrong, not the provider), so is an answer over `MAX_RESPONSE_BYTES` (502
    `UPSTREAM_UNREACHABLE`); a timeout is `UPSTREAM_TIMEOUT`.
    """
    if isinstance(error, InsecureUrl):
        return UpstreamFailure("blocked", insecure_url())
    if isinstance(error, BlockedAddress):
        return UpstreamFailure("blocked", upstream_blocked())
    if isinstance(error, (TimeoutError, httpx.TimeoutException)):
        return UpstreamFailure("timeout", upstream_timeout(timeout_ms))
    if isinstance(error, httpx.TransportError):
        return UpstreamFailure("connection_error", None)
    if isinstance(error, ResponseTooLarge):
        too_large = GatewayError(502, "UPSTREAM_UNREACHABLE", _TOO_LARGE_MESSAGE)
        return UpstreamFailure("blocked", too_large, label="response_too_large")
    return None


def parse_retry_after(headers: Mapping[str, str]) -> float | None:
    """The provider's wait in seconds: `retry-after-ms` (÷ 1000) when present and valid, else
    `retry-after` (an integer or a decimal). An HTTP date or junk is None.

    `retry-after-ms` comes first because it is the precise value: `retry-after` is whole seconds
    rounded up, and clients that follow the official SDKs wait for the millisecond one.
    """
    milliseconds = _non_negative_number(headers.get("retry-after-ms"))
    if milliseconds is not None:
        return milliseconds / 1000
    return _non_negative_number(headers.get("retry-after"))


def _non_negative_number(value: str | None) -> float | None:
    if value is None or len(value) > 32:
        return None
    try:
        number = float(value.strip())
    except ValueError:
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return number


def insecure_url() -> GatewayError:
    error = upstream_blocked()
    return dataclasses.replace(
        error,
        message=_INSECURE_URL_MESSAGE,
        openai=ErrorKind("server_error", message=_INSECURE_URL_MESSAGE),
        anthropic=ErrorKind("api_error", message=_INSECURE_URL_MESSAGE),
    )


async def read_all(response: UpstreamResponse) -> bytes:
    buffer = bytearray()
    try:
        async for chunk in response.body:
            buffer += chunk
            if len(buffer) > MAX_RESPONSE_BYTES:
                raise ResponseTooLarge
    finally:
        await response.aclose()
    return bytes(buffer)


async def idle_bounded(
    first: bytes | None, rest: AsyncIterator[bytes], response: UpstreamResponse
) -> AsyncIterator[bytes]:
    """The stream from its first chunk on; a gap over `STREAM_IDLE_TIMEOUT_S` raises TimeoutError.

    Closes the upstream response however the stream ends.
    """
    try:
        if first is None:
            return
        yield first
        while True:
            try:
                async with asyncio.timeout(STREAM_IDLE_TIMEOUT_S):
                    chunk = await anext(rest)
            except StopAsyncIteration:
                return
            yield chunk
    finally:
        await close_quietly(response)


async def close_quietly(response: UpstreamResponse | None) -> None:
    if response is not None:
        with contextlib.suppress(Exception):
            await response.aclose()
