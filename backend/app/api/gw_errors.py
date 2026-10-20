"""Error responses under `/gw/`: the imitated provider's envelope, never problem+json.

An OpenAI or Anthropic SDK pointed at the gateway parses errors in its provider's shape, so
everything under `/gw/` that is not an upstream answer renders through
`app.gateway.errors.render_error`: gateway errors, an unknown path or method, a request the
framework cannot validate, a database too busy to check the key, and any unexpected exception.
The envelope follows the path's surface (`surface_for_path`, then `envelope_for`), never the
header the key came in.

`install_gateway_error_handlers` wraps the handlers already installed on the app (the dashboard's
problem+json ones from `app.core.errors`), so call it after `install_error_handlers`. A wrapped
handler looks at the path first: outside `/gw/` it hands the exception to the handler it
replaced, so dashboard and ingestion errors keep their shape.
"""

import uuid
from collections.abc import Awaitable, Callable
from typing import cast

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response

from app.core.db_errors import is_busy, is_statement_timeout
from app.core.errors import RETRY_AFTER_SECONDS, ProblemError
from app.core.observability import GATEWAY_REQUESTS
from app.core.request_context import current_request_id
from app.gateway.errors import (
    ErrorKind,
    GatewayError,
    envelope_for,
    internal,
    invalid_request,
    method_not_allowed,
    render_error,
    request_too_large,
    route_not_found,
    service_unavailable,
    surface_for_path,
)

GATEWAY_PATH_PREFIX = "/gw/"

logger = structlog.get_logger(__name__)

Handler = Callable[[Request, Exception], Awaitable[Response]]


def is_gateway_path(path: str) -> bool:
    return path == GATEWAY_PATH_PREFIX.rstrip("/") or path.startswith(GATEWAY_PATH_PREFIX)


def request_id_of(request: Request) -> str:
    """The id the request-context middleware gave the request (a fresh one outside it)."""
    return current_request_id() or uuid.uuid4().hex


def gateway_error_response(request: Request, error: GatewayError) -> JSONResponse:
    """`error` in the envelope of the request's path, with the gateway's error headers."""
    envelope = envelope_for(surface_for_path(request.url.path), request.headers)
    rendered = render_error(error, envelope, request_id_of(request))
    return JSONResponse(rendered.body, status_code=rendered.status, headers=rendered.headers)


def _from_status(status: int, code: str, message: str) -> GatewayError:
    """A gateway error for an HTTP-layer status that has no gateway code of its own."""
    kind = ErrorKind("server_error") if status >= 500 else ErrorKind("invalid_request_error")
    anthropic = ErrorKind("api_error") if status >= 500 else ErrorKind("invalid_request_error")
    return GatewayError(status, code, message, openai=kind, anthropic=anthropic)


def _http_error(request: Request, exc: StarletteHTTPException) -> GatewayError:
    method, path = request.method, request.url.path
    if exc.status_code == 404:
        return route_not_found(method, path)
    if exc.status_code == 405:
        return method_not_allowed(method, path, (exc.headers or {}).get("Allow"))
    if exc.status_code == 413:
        return request_too_large()
    detail = exc.detail if isinstance(exc.detail, str) else "The request could not be served."
    return _from_status(exc.status_code, "ERROR", detail)


def _validation_error(exc: RequestValidationError) -> GatewayError:
    errors = exc.errors()
    location = [str(part) for part in errors[0].get("loc", ())] if errors else []
    if location and location[0] in {"body", "query", "path", "header", "cookie"}:
        location = location[1:]
    return invalid_request("The request is invalid.", param=".".join(location) or None)


def _database_error(request: Request, exc: Exception) -> GatewayError:
    """Pool exhaustion, a statement timeout, a lost connection or an unreachable database is a
    503 with `Retry-After`; anything else a 500.
    """
    if is_busy(exc):
        if isinstance(exc, PoolTimeoutError):
            event = "db_pool_timeout"
        elif isinstance(exc, DBAPIError) and exc.connection_invalidated:
            event = "db_connection_lost"
        elif isinstance(exc, OperationalError) and is_statement_timeout(exc.orig):
            event = "db_statement_timeout"
        else:
            event = "db_unreachable"
        logger.warning(event, method=request.method, path=request.url.path)
        return service_unavailable(RETRY_AFTER_SECONDS)
    return _unexpected(request, exc)


def _unexpected(request: Request, exc: Exception) -> GatewayError:
    # Logged with its traceback, like the dashboard's 500s; the client gets only the request id.
    logger.exception("gateway.unhandled_exception", error_type=type(exc).__name__)
    return internal(request_id_of(request))


def _translate(request: Request, exc: Exception) -> GatewayError:
    if isinstance(exc, GatewayError):
        return exc
    if isinstance(exc, StarletteHTTPException):
        return _http_error(request, exc)
    if isinstance(exc, RequestValidationError):
        return _validation_error(exc)
    if isinstance(exc, (PoolTimeoutError, OperationalError)):
        return _database_error(request, exc)
    if isinstance(exc, ProblemError):
        return _from_status(exc.status, exc.code, exc.detail or exc.code)
    return _unexpected(request, exc)


def _scoped(previous: Handler | None) -> Handler:
    """A handler that renders under `/gw/` and defers to `previous` everywhere else."""

    async def handle(request: Request, exc: Exception) -> Response:
        if is_gateway_path(request.url.path):
            if isinstance(exc, GatewayError):
                # A refusal before `execute` (key, limits, body), which counts its own calls.
                surface = surface_for_path(request.url.path)
                GATEWAY_REQUESTS.labels(surface, "gateway_error").inc()
            return gateway_error_response(request, _translate(request, exc))
        if previous is None:
            raise exc
        return await previous(request, exc)

    return handle


# Every exception class the dashboard handles, plus the gateway's own. `Exception` is the one
# Starlette's outermost middleware renders for unexpected errors.
_HANDLED: tuple[type[Exception], ...] = (
    GatewayError,
    ProblemError,
    RequestValidationError,
    StarletteHTTPException,
    PoolTimeoutError,
    OperationalError,
    Exception,
)


def install_gateway_error_handlers(app: FastAPI) -> None:
    """Render every error under `/gw/` in the provider envelope (see the module docstring)."""
    for exception_class in _HANDLED:
        previous = cast(Handler | None, app.exception_handlers.get(exception_class))
        app.add_exception_handler(exception_class, _scoped(previous))
