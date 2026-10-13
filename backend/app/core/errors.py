"""RFC 9457 problem details.

Every error response produced by the API has the shape::

    {"type": "about:blank", "title": "Not Found", "status": 404,
     "detail": "...", "code": "NOT_FOUND", "request_id": "...", "errors": [...]}
"""

from collections.abc import Mapping, Sequence
from http import HTTPStatus
from typing import Any, TypedDict, cast

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DataError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.db_errors import is_nul_error, is_statement_timeout
from app.core.request_context import current_request_id

PROBLEM_CONTENT_TYPE = "application/problem+json"

logger = structlog.get_logger(__name__)


class FieldError(TypedDict):
    """A single field-level validation message."""

    field: str
    message: str


class ProblemError(Exception):
    """An error that maps directly onto a problem+json response.

    `extensions` are RFC 9457 extension members: extra top-level fields a client can read
    without parsing `detail`, such as the current version in a version conflict. They never
    replace a standard member.
    """

    def __init__(
        self,
        status: int,
        code: str,
        detail: str | None = None,
        *,
        errors: Sequence[FieldError] | None = None,
        headers: Mapping[str, str] | None = None,
        extensions: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(detail or code)
        self.status = status
        self.code = code
        self.detail = detail
        self.errors = list(errors) if errors else None
        self.headers = dict(headers) if headers else None
        self.extensions = dict(extensions) if extensions else None


def not_found(detail: str = "The requested resource was not found.") -> ProblemError:
    return ProblemError(404, "NOT_FOUND", detail)


def forbidden(detail: str = "You do not have permission to perform this action.") -> ProblemError:
    return ProblemError(403, "FORBIDDEN", detail)


def unauthorized(detail: str = "Authentication is required.") -> ProblemError:
    return ProblemError(401, "UNAUTHORIZED", detail)


def key_scope(detail: str) -> ProblemError:
    """403 for an API key used where its scopes (or its kind) do not reach."""
    return ProblemError(403, "KEY_SCOPE", detail)


def key_expired() -> ProblemError:
    return ProblemError(401, "KEY_EXPIRED", "This API key has expired. Create a new one.")


def token_scope(detail: str) -> ProblemError:
    """403 for a read-only personal access token used where only a write token will do."""
    return ProblemError(403, "TOKEN_SCOPE", detail)


def token_expired() -> ProblemError:
    return ProblemError(401, "TOKEN_EXPIRED", "This access token has expired. Create a new one.")


def session_required(detail: str = "This route needs a signed-in session.") -> ProblemError:
    """403 for a personal access token used on a route that only a browser session may use."""
    return ProblemError(403, "SESSION_REQUIRED", detail)


def conflict(code: str, detail: str) -> ProblemError:
    return ProblemError(409, code, detail)


def confirmation_mismatch(detail: str) -> ProblemError:
    """422 for a destructive request whose typed confirmation is not the exact slug it names."""
    return ProblemError(422, "CONFIRMATION_MISMATCH", detail)


def idempotency_mismatch() -> ProblemError:
    """422 for an `Idempotency-Key` that was first used for a different request."""
    return ProblemError(
        422,
        "IDEMPOTENCY_MISMATCH",
        "This Idempotency-Key was already used for a different request. "
        "Send a new key for a different request.",
    )


def idempotency_in_progress() -> ProblemError:
    """409 for an `Idempotency-Key` whose first request has not answered yet."""
    return ProblemError(
        409,
        "IDEMPOTENCY_IN_PROGRESS",
        "A request with this Idempotency-Key is still being processed. Retry in a moment.",
    )


def not_configured(detail: str) -> ProblemError:
    """409 for a request that needs an optional integration the operator has not set up.

    `detail` names the setting to change, so the operator knows what to do.
    """
    return ProblemError(409, "NOT_CONFIGURED", detail)


def too_many_requests(retry_after_seconds: int, detail: str) -> ProblemError:
    return ProblemError(
        429,
        "RATE_LIMITED",
        detail,
        headers={"Retry-After": str(max(1, retry_after_seconds))},
    )


# How long a client is told to wait after a 503. The pool and the statement limits are seconds
# long, so a retry a few seconds later finds the database less busy.
RETRY_AFTER_SECONDS = 5


def service_unavailable(
    detail: str = "The service is too busy to answer right now. Try again in a few seconds.",
    *,
    retry_after_seconds: int = RETRY_AFTER_SECONDS,
) -> ProblemError:
    """503 for a request the database could not take or finish in time; see `Retry-After`."""
    return ProblemError(
        503,
        "SERVICE_UNAVAILABLE",
        detail,
        headers={"Retry-After": str(max(1, retry_after_seconds))},
    )


def problem_response(
    status: int,
    code: str,
    detail: str | None = None,
    *,
    errors: Sequence[FieldError] | None = None,
    headers: Mapping[str, str] | None = None,
    extensions: Mapping[str, Any] | None = None,
) -> JSONResponse:
    # Extension members first, so a standard member below always wins over one of the same name.
    body: dict[str, Any] = dict(extensions) if extensions else {}
    body |= {
        "type": "about:blank",
        "title": _title_for(status),
        "status": status,
        "detail": detail,
        "code": code,
        "request_id": current_request_id(),
    }
    if errors:
        body["errors"] = list(errors)
    return JSONResponse(
        body,
        status_code=status,
        media_type=PROBLEM_CONTENT_TYPE,
        headers=dict(headers) if headers else None,
    )


def _title_for(status: int) -> str:
    try:
        return HTTPStatus(status).phrase
    except ValueError:
        return "Error"


_CODES_BY_STATUS = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    413: "PAYLOAD_TOO_LARGE",
    415: "UNSUPPORTED_MEDIA_TYPE",
    422: "VALIDATION_ERROR",
    429: "RATE_LIMITED",
    503: "SERVICE_UNAVAILABLE",
}


def _validation_field(location: Sequence[int | str]) -> str:
    # Drop the leading "body"/"query"/"path" segment; it is noise for clients.
    parts = [str(part) for part in location]
    if parts and parts[0] in {"body", "query", "path", "header", "cookie"}:
        parts = parts[1:]
    return ".".join(parts) or "request"


# Starlette types every handler as (Request, Exception); each is registered
# for exactly one exception class, so the casts below are safe.
async def _handle_problem(_: Request, exc: Exception) -> JSONResponse:
    problem = cast(ProblemError, exc)
    return problem_response(
        problem.status,
        problem.code,
        problem.detail,
        errors=problem.errors,
        headers=problem.headers,
        extensions=problem.extensions,
    )


async def _handle_validation(_: Request, exc: Exception) -> JSONResponse:
    validation = cast(RequestValidationError, exc)
    errors = [
        FieldError(
            field=_validation_field(error.get("loc", ())),
            message=str(error.get("msg", "Invalid value")),
        )
        for error in validation.errors()
    ]
    return problem_response(422, "VALIDATION_ERROR", "The request is invalid.", errors=errors)


async def _handle_http(_: Request, exc: Exception) -> JSONResponse:
    http_error = cast(StarletteHTTPException, exc)
    code = _CODES_BY_STATUS.get(http_error.status_code, "ERROR")
    detail = http_error.detail if isinstance(http_error.detail, str) else None
    return problem_response(http_error.status_code, code, detail, headers=http_error.headers)


async def _handle_unexpected(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled_exception", error_type=type(exc).__name__)
    return problem_response(500, "INTERNAL_ERROR", "An unexpected error occurred.")


async def _busy(event: str, request: Request) -> JSONResponse:
    """The 503 for an overloaded database, logged under a name an operator can alert on."""
    logger.warning(event, method=request.method, path=request.url.path)
    return await _handle_problem(request, service_unavailable())


async def _handle_pool_timeout(request: Request, _: Exception) -> JSONResponse:
    # No connection came free within the pool timeout: the database is behind.
    return await _busy("db_pool_timeout", request)


async def _handle_operational_error(request: Request, exc: Exception) -> JSONResponse:
    error = cast(OperationalError, exc)
    if is_statement_timeout(error.orig):
        return await _busy("db_statement_timeout", request)
    return await _handle_unexpected(request, exc)


async def _handle_data_error(request: Request, exc: Exception) -> JSONResponse:
    error = cast(DataError, exc)
    # A request value Postgres text cannot hold (NUL) that no parameter constraint caught: the
    # client's mistake, not ours. Any other data error is unexpected.
    if is_nul_error(error.orig):
        return problem_response(
            422, "VALIDATION_ERROR", "Text parameters cannot contain NUL characters."
        )
    return await _handle_unexpected(request, exc)


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ProblemError, _handle_problem)
    app.add_exception_handler(RequestValidationError, _handle_validation)
    app.add_exception_handler(StarletteHTTPException, _handle_http)
    app.add_exception_handler(PoolTimeoutError, _handle_pool_timeout)
    app.add_exception_handler(OperationalError, _handle_operational_error)
    app.add_exception_handler(DataError, _handle_data_error)
    app.add_exception_handler(Exception, _handle_unexpected)
