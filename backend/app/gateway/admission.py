"""Immediate per-process HTTP gateway admission, held through stream finalization."""

import hashlib
import time

from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from app.api.gw import MAX_REQUEST_BYTES, presented_key
from app.api.gw_errors import gateway_error_response, is_gateway_path
from app.core.observability import (
    GATEWAY_ADMISSION_DURATION,
    GATEWAY_ADMISSION_REJECTIONS,
    GATEWAY_IN_FLIGHT,
)
from app.gateway.errors import GatewayError, service_unavailable

BODYLESS_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def reserved_bytes(method: str, headers: Headers) -> int:
    """The declared body size, or the full request cap when it is undeclared or over the cap."""
    if "transfer-encoding" in headers:
        return MAX_REQUEST_BYTES  # framed by Transfer-Encoding, whatever Content-Length says
    if method in BODYLESS_METHODS:
        return 0
    declared = headers.get("content-length", "")
    # The length bound keeps int() under its digit limit; anything longer is over the cap anyway.
    if (
        len(declared) <= 20
        and declared.isascii()
        and declared.isdigit()
        and int(declared) <= MAX_REQUEST_BYTES
    ):
        return int(declared)
    # Over the cap still reserves the cap; the handler's bounded read answers 413.
    return MAX_REQUEST_BYTES


def credential_of(headers: Headers) -> str:
    """The key authentication will read, so each key gets one share however it is sent."""
    try:
        return presented_key(headers)
    except GatewayError:  # missing, blank or conflicting: still one bucket per raw value
        return (headers.get("authorization") or headers.get("x-api-key") or "").strip()


class GatewayAdmissionMiddleware:
    """Bound HTTP ingress without queueing or reading rejected request bodies.

    A call is admitted only while the count, the reserved body bytes and the caller's key share
    are all under their limits, checked in that order from headers alone; all three are held
    through the complete ASGI call (stream finalization included) and released exactly once.
    One event loop owns the counters. Internal playground and worker execution never enters
    this HTTP middleware; dashboard admission and health retain their independent capacity.
    """

    def __init__(self, app: ASGIApp, limit: int, limit_bytes: int, limit_per_key: int) -> None:
        self.app = app
        self.limit = limit
        self.limit_bytes = limit_bytes
        self.limit_per_key = limit_per_key
        self.in_flight = 0
        self.in_flight_bytes = 0
        # Keyed by a digest so no credential stays in memory; an entry leaves at zero.
        self.per_key: dict[bytes, int] = {}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not is_gateway_path(scope["path"]):
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        reserved = reserved_bytes(scope["method"], headers)
        key = hashlib.sha256(credential_of(headers).encode("latin-1")).digest()
        held = self.per_key.get(key, 0)
        if self.in_flight >= self.limit:
            reason = "count"
        elif self.in_flight_bytes + reserved > self.limit_bytes:
            reason = "bytes"
        elif held >= self.limit_per_key:
            reason = "key"
        else:
            reason = ""
        if reason:
            GATEWAY_ADMISSION_REJECTIONS.labels(reason=reason).inc()
            response = gateway_error_response(Request(scope), service_unavailable(1))
            await response(scope, receive, send)
            return
        self.in_flight += 1
        self.in_flight_bytes += reserved
        self.per_key[key] = held + 1
        GATEWAY_IN_FLIGHT.inc()
        started = time.perf_counter()
        try:
            await self.app(scope, receive, send)
        finally:
            # The inner ASGI call owns provider closure and accounting, including cancellation.
            self.in_flight -= 1
            self.in_flight_bytes -= reserved
            if (left := self.per_key[key] - 1) > 0:
                self.per_key[key] = left
            else:
                del self.per_key[key]
            GATEWAY_IN_FLIGHT.dec()
            GATEWAY_ADMISSION_DURATION.observe(time.perf_counter() - started)
