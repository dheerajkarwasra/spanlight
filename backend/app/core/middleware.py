"""ASGI middleware: request ids, access logging/metrics, bearer requests and the Origin check."""

import re
import time
import uuid
from collections.abc import Collection

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.errors import problem_response
from app.core.observability import HTTP_LATENCY, HTTP_REQUESTS
from app.core.request_context import AuditContext, set_audit_context, set_request_id
from app.core.security import is_bearer

REQUEST_ID_HEADER = "x-request-id"
_REQUEST_ID_HEADER_BYTES = REQUEST_ID_HEADER.encode()
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

logger = structlog.get_logger("app.access")


def _header(scope: Scope, name: str) -> str | None:
    target = name.encode()
    for key, value in scope.get("headers", []):
        if key == target:
            return bytes(value).decode("latin-1")
    return None


def _carries_bearer(scope: Scope) -> bool:
    """Whether the request authenticates with `Authorization: Bearer …`, even an empty one.

    The scheme alone decides, so a client cannot keep its cookies in play by sending a bearer
    the API then fails to understand: that request is refused as unauthenticated instead.
    """
    return is_bearer(_header(scope, "authorization"))


def _route_template(scope: Scope) -> str:
    """The matched route path (e.g. `/api/v1/projects/{project_id}`), to bound label cardinality.

    The router records the matched route in the (shared) scope while routing. FastAPI applies
    the prefix of an included router lazily, so `route.path` is relative to the router that
    declared it (`/projects/{project_id}`); the effective route context FastAPI stores next to
    it carries the full path. Without that context the route's own path is used.
    """
    context = scope.get("fastapi", {}).get("effective_route_context")
    full_path = getattr(context, "path", None)
    if isinstance(full_path, str) and full_path:
        return full_path
    route = scope.get("route")
    return str(getattr(route, "path", "unmatched"))


class RequestContextMiddleware:
    """Assigns a request id, logs one line per request and records RED metrics."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = _header(scope, REQUEST_ID_HEADER)
        request_id = (
            incoming if incoming and _VALID_REQUEST_ID.match(incoming) else uuid.uuid4().hex
        )
        set_request_id(request_id)
        client = scope.get("client")
        user_agent = _header(scope, "user-agent")
        set_audit_context(
            AuditContext(
                ip=client[0] if client else None,
                user_agent=user_agent[:512] if user_agent else None,
            )
        )
        started = time.perf_counter()
        status_code = 500

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = list(message.get("headers", []))
                # The gateway sets the same id itself (it is in its error bodies too); a second
                # copy would read as "id, id" to clients that join repeated headers.
                if not any(name.lower() == _REQUEST_ID_HEADER_BYTES for name, _ in headers):
                    headers.append((_REQUEST_ID_HEADER_BYTES, request_id.encode()))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            elapsed = time.perf_counter() - started
            method = scope["method"]
            route = _route_template(scope)
            HTTP_REQUESTS.labels(method, route, str(status_code)).inc()
            HTTP_LATENCY.labels(method, route).observe(elapsed)
            logger.info(
                "http_request",
                method=method,
                path=scope["path"],
                route=route,
                status=status_code,
                duration_ms=round(elapsed * 1000, 2),
            )
            # The request id is deliberately left set: Starlette's outermost
            # ServerErrorMiddleware renders unhandled 500s after we return and
            # still needs it for the problem body.


class BearerRequestMiddleware:
    """A request with an `Authorization: Bearer …` header is authenticated by that header alone.

    Cookies are an ambient credential: a browser attaches them to requests another site caused,
    which is why cookie-authenticated requests need CSRF protection. A bearer credential is not:
    only the code holding it can send it. So when a bearer is present on an `/api` request the
    `Cookie` header is dropped before routing. Nothing downstream can then read a session or
    CSRF cookie by mistake, and the API never falls back to the cookie when the bearer is wrong.
    `OriginCheckMiddleware` exempts the same requests.

    Only the Bearer scheme counts. Browsers attach Basic and Negotiate credentials by
    themselves when a proxy in front of the app asks for them, so those are ambient too: such a
    request keeps its cookies and its Origin check.

    The scope is changed in place rather than copied: the router records the matched route in
    the scope it is given, and the request-context middleware reads it back from its own.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"].startswith("/api/") and _carries_bearer(scope):
            scope["headers"] = [
                (name, value) for name, value in scope.get("headers", []) if name != b"cookie"
            ]
        await self.app(scope, receive, send)


class OriginCheckMiddleware:
    """Rejects state-changing `/api` requests whose Origin is not allowlisted.

    This is the first CSRF layer (the double-submit token is the second) and
    the only one for signup/login, which happen before a session exists.
    Requests without an Origin header are rejected too: every browser sends it
    on unsafe methods, so its absence means a non-browser client, which should
    send `Authorization: Bearer …` instead.

    A request with a bearer is exempt. CSRF abuses credentials the browser attaches by itself,
    and this API never reads cookies on such a request (`BearerRequestMiddleware`), and every
    `/api/v1` route answers a bearer with an authentication result or a refusal, never with
    anonymous service. A page on another site cannot add the header either: it is not one a
    cross-origin request may carry without a CORS preflight, and this API sends no CORS
    headers, so the browser stops the request before it is sent.
    """

    def __init__(self, app: ASGIApp, allowed_origins: Collection[str]) -> None:
        self.app = app
        self.allowed_origins = frozenset(allowed_origins)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] == "http"
            and scope["method"] in _UNSAFE_METHODS
            and scope["path"].startswith("/api/")
            and not _carries_bearer(scope)
        ):
            origin = _header(scope, "origin")
            if origin is None or origin.rstrip("/") not in self.allowed_origins:
                response = problem_response(
                    403, "ORIGIN_NOT_ALLOWED", "The request origin is not allowed."
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)
