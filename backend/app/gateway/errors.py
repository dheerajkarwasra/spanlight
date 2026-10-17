"""Gateway error envelopes: provider-shaped error bodies for everything under ``/gw/``.

Pure module. A ``GatewayError`` carries a stable ``spanlight_code``; ``render_error`` turns it
into the OpenAI or Anthropic envelope. Status and per-envelope type/code come from one table
(``_TABLE``), so call sites never branch on the envelope. Callers that need provider-specific
wording (fault profiles) pass explicit ``ErrorKind`` overrides instead of editing the table.
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

Surface = Literal["chat_completions", "responses", "messages", "models"]
Envelope = Literal["openai", "anthropic"]
KeyLimitKind = Literal["rpm", "tpm", "org"]
"""Which gateway key limit refused a call (not `app.ingest.rate_limit_kind.RateLimitKind`)."""


def surface_for_path(path: str) -> Surface:
    """Map a request path to its surface; anything unrecognised is chat completions."""
    tail = path.rstrip("/").rsplit("/", 1)[-1]
    if tail == "messages":
        return "messages"
    if tail == "responses":
        return "responses"
    if tail == "models":
        return "models"
    return "chat_completions"


def envelope_for(surface: Surface, headers: Mapping[str, str]) -> Envelope:
    """Pick the error envelope. ``models`` is Anthropic when ``anthropic-version`` is sent."""
    if surface == "messages":
        return "anthropic"
    if surface == "models" and any(name.lower() == "anthropic-version" for name in headers):
        return "anthropic"
    return "openai"


@dataclass(frozen=True)
class ErrorKind:
    """How one envelope names an error; ``message`` overrides the error's own.

    ``code`` is rendered in the OpenAI envelope only. A passed-through provider error is never
    rendered, so its kinds carry the provider's own code (OpenAI's ``error.code`` or Anthropic's
    ``error.details.error_code``) for the span in both envelopes.
    """

    type: str
    code: str | None = None
    message: str | None = None


@dataclass(frozen=True)
class _Row:
    status: int
    openai: ErrorKind
    anthropic: ErrorKind


_INVALID = ErrorKind("invalid_request_error")
_SERVER = ErrorKind("server_error")
_API = ErrorKind("api_error")

_TABLE: dict[str, _Row] = {
    "UNAUTHORIZED": _Row(
        401,
        ErrorKind("invalid_request_error", "invalid_api_key"),
        ErrorKind("authentication_error"),
    ),
    "RATE_LIMITED": _Row(
        429,
        ErrorKind("requests", "rate_limit_exceeded"),
        ErrorKind("rate_limit_error"),
    ),
    "BUDGET_EXCEEDED": _Row(
        402,
        ErrorKind("insufficient_quota", "insufficient_quota"),
        ErrorKind("billing_error"),
    ),
    "MODEL_NOT_ALLOWED": _Row(
        404,
        ErrorKind("invalid_request_error", "model_not_found"),
        ErrorKind("not_found_error"),
    ),
    "NO_COMPATIBLE_TARGET": _Row(400, _INVALID, _INVALID),
    "INVALID_REQUEST": _Row(400, _INVALID, _INVALID),
    "PAYLOAD_TOO_LARGE": _Row(413, _INVALID, ErrorKind("request_too_large")),
    "UPSTREAM_UNREACHABLE": _Row(502, _SERVER, _API),
    "UPSTREAM_BLOCKED": _Row(502, _SERVER, _API),
    "UPSTREAM_TIMEOUT": _Row(504, _SERVER, ErrorKind("timeout_error")),
    "INTERNAL_ERROR": _Row(500, _SERVER, _API),
}


@dataclass(eq=False)
class GatewayError(Exception):
    """A gateway-produced error. Treat as immutable: not ``frozen`` because a frozen dataclass
    exception breaks when Python sets ``__traceback__``/``__cause__`` (e.g. in context managers).

    ``openai`` / ``anthropic`` replace the table's kind for that envelope (used by fault
    profiles, which copy the provider's wording); both default to the table row for
    ``spanlight_code``. ``extra_headers`` are added to the rendered headers.
    """

    status: int
    spanlight_code: str
    message: str
    retry_after: float | None = None
    param: str | None = None
    openai: ErrorKind | None = None
    anthropic: ErrorKind | None = None
    extra_headers: tuple[tuple[str, str], ...] = ()

    def __str__(self) -> str:
        return f"{self.spanlight_code}: {self.message}"


def _error(
    code: str,
    message: str,
    *,
    param: str | None = None,
    retry_after: float | None = None,
    openai: ErrorKind | None = None,
) -> GatewayError:
    return GatewayError(
        _TABLE[code].status, code, message, retry_after=retry_after, param=param, openai=openai
    )


def invalid_key(conflicting: bool = False) -> GatewayError:
    message = (
        "Authorization and x-api-key carry different keys."
        if conflicting
        else "Invalid or missing Spanlight gateway key."
    )
    return _error("UNAUTHORIZED", message)


def rate_limited(
    kind: KeyLimitKind, limit: int, retry_after: float, key_prefix: str | None = None
) -> GatewayError:
    who = f"gateway key {key_prefix}" if key_prefix else "this gateway key"
    messages = {
        "rpm": f"Rate limit reached for {who}: {limit} requests per minute.",
        "tpm": f"Rate limit reached for {who}: {limit} tokens per minute.",
        "org": f"Rate limit reached for this organization: {limit} requests per minute.",
    }
    openai = ErrorKind("tokens" if kind == "tpm" else "requests", "rate_limit_exceeded")
    return _error("RATE_LIMITED", messages[kind], retry_after=retry_after, openai=openai)


def budget_exceeded(reason: str) -> GatewayError:
    return _error("BUDGET_EXCEEDED", f"Blocked by a Spanlight budget: {reason}.")


def model_not_allowed(model: str) -> GatewayError:
    message = f"The model '{model}' is not allowed for this gateway key."
    return _error("MODEL_NOT_ALLOWED", message, param="model")


def no_compatible_target(route: str, path: str) -> GatewayError:
    return _error("NO_COMPATIBLE_TARGET", f"Route '{route}' has no target that serves {path}.")


def invalid_request(
    message: str = "Request body must be a JSON object with a string 'model'.",
    param: str | None = "model",
) -> GatewayError:
    return _error("INVALID_REQUEST", message, param=param)


def request_too_large() -> GatewayError:
    return _error("PAYLOAD_TOO_LARGE", "Request body exceeds the 10 MB limit.")


def upstream_unreachable(attempts: int) -> GatewayError:
    return _error(
        "UPSTREAM_UNREACHABLE", f"Could not reach the provider after {attempts} attempts."
    )


def upstream_redirect() -> GatewayError:
    message = "The provider answered with a redirect, which the gateway does not follow."
    return _error("UPSTREAM_UNREACHABLE", message)


def upstream_blocked() -> GatewayError:
    message = (
        "The provider address resolves to a private or local network, "
        "which this server does not allow."
    )
    return _error("UPSTREAM_BLOCKED", message)


def upstream_timeout(timeout_ms: int) -> GatewayError:
    message = f"The provider did not respond within the route's {timeout_ms} ms budget."
    return _error("UPSTREAM_TIMEOUT", message)


def internal(request_id: str) -> GatewayError:
    return _error("INTERNAL_ERROR", f"Spanlight gateway error. Request ID {request_id}.")


# Errors of the HTTP layer under `/gw/`: an unknown path or method, and a database too busy to
# check the key. Not in `_TABLE` (no gateway step produces them), so they carry their kinds.
# The wording follows the providers' own answers to the same requests.


MAX_ECHOED_PATH = 200


def route_not_found(method: str, path: str) -> GatewayError:
    return GatewayError(
        404,
        "NOT_FOUND",
        f"Invalid URL ({method} {path[:MAX_ECHOED_PATH]}).",
        openai=ErrorKind("invalid_request_error"),
        anthropic=ErrorKind("not_found_error"),
    )


def method_not_allowed(method: str, path: str, allow: str | None) -> GatewayError:
    return GatewayError(
        405,
        "METHOD_NOT_ALLOWED",
        f"Method {method} is not allowed on {path}.",
        openai=ErrorKind("invalid_request_error"),
        anthropic=ErrorKind("invalid_request_error"),
        extra_headers=(("Allow", allow),) if allow else (),
    )


def service_unavailable(retry_after: float) -> GatewayError:
    return GatewayError(
        503,
        "SERVICE_UNAVAILABLE",
        "The gateway is too busy to answer right now. Try again in a few seconds.",
        retry_after=retry_after,
        openai=ErrorKind("server_error"),
        anthropic=ErrorKind("overloaded_error"),
    )


@dataclass(frozen=True)
class RenderedError:
    status: int
    body: dict[str, Any]
    headers: dict[str, str]


def render_error(error: GatewayError, envelope: Envelope, request_id: str) -> RenderedError:
    """Render ``error`` in the provider envelope, with the gateway's error headers."""
    row = _TABLE.get(error.spanlight_code)
    kind = error.openai if envelope == "openai" else error.anthropic
    if kind is None:
        # Unknown codes without overrides render as a server error rather than raising.
        fallback = _Row(error.status, _SERVER, _API)
        kind = (row or fallback).openai if envelope == "openai" else (row or fallback).anthropic
    message = kind.message or error.message

    body: dict[str, Any]
    if envelope == "openai":
        body = {
            "error": {
                "message": message,
                "type": kind.type,
                "param": error.param,
                "code": kind.code,
                "spanlight_code": error.spanlight_code,
            }
        }
    else:
        body = {
            "type": "error",
            "error": {
                "type": kind.type,
                "message": message,
                "spanlight_code": error.spanlight_code,
            },
            "request_id": request_id,
        }

    headers = {"X-Request-ID": request_id, "X-Spanlight-Code": error.spanlight_code}
    if error.retry_after is not None:
        # Whole seconds, rounded up and at least 1: `Retry-After: 0` makes clients spin.
        headers["Retry-After"] = str(max(1, math.ceil(error.retry_after)))
    headers.update(error.extra_headers)
    return RenderedError(error.status, body, headers)
