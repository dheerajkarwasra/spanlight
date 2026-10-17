"""What a provider's error response says: error type and code, retry-after, rate-limit headers.

The official SDKs raise ``APIStatusError`` with the HTTP ``response`` and the parsed ``body``.
OpenAI keeps the inner error object as ``body`` (``{"type", "code", ...}``); Anthropic keeps the
whole envelope (``{"type": "error", "error": {"type", "details": {"error_code"}}}``) and also
exposes ``error.type``. Both shapes are read here. Connection and timeout errors carry no
response and record nothing.

An app pointed at the Spanlight gateway can be refused by the gateway itself (its gateway key's
rate limit, a budget, a blocked upstream). The gateway names such an answer in an
``X-Spanlight-Code`` header; the whole response, envelope and headers, is then the gateway's, so
the code is recorded as ``spanlight.gateway.error_code`` and no ``provider.*`` field is set.
"""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Mapping
from typing import Any

from .._ratelimit_headers import snapshot_headers
from .._span import Span

logger = logging.getLogger("spanlight")

ERROR_TYPE = "provider.error.type"
ERROR_CODE = "provider.error.code"
RATELIMIT = "provider.ratelimit"
RETRY_AFTER_S = "provider.retry_after_s"
GATEWAY_ERROR_CODE = "spanlight.gateway.error_code"

# The gateway's own error codes (the server's `app/gateway/errors.py`).
# `UPSTREAM_ERROR` is absent on purpose: it marks a provider error the gateway passed through
# unchanged, so its envelope and headers are the provider's.
GATEWAY_CODES = frozenset(
    {
        "INVALID_REQUEST",
        "NO_COMPATIBLE_TARGET",
        "MODEL_NOT_ALLOWED",
        "PAYLOAD_TOO_LARGE",
        "NOT_FOUND",
        "METHOD_NOT_ALLOWED",
        "UNAUTHORIZED",
        "RATE_LIMITED",
        "BUDGET_EXCEEDED",
        "UPSTREAM_TIMEOUT",
        "UPSTREAM_UNREACHABLE",
        "UPSTREAM_BLOCKED",
        "INTERNAL_ERROR",
        "SERVICE_UNAVAILABLE",
    }
)
# A lab fault the gateway injected answers with `FAULT_<SCENARIO>` (`gateway/faults.py`); it is
# gateway-made too. Upper case, digits and underscores, at most the error-field length.
_FAULT_CODE = re.compile(r"FAULT_[A-Z0-9_]{1,58}")
_GATEWAY_CODE_HEADER = "x-spanlight-code"
# On an error the gateway passed through, `x-request-id` is the gateway's own id and the
# provider's travels in this header (the server's `gateway/adapters/base.py`).
_UPSTREAM_REQUEST_ID_HEADER = "x-upstream-request-id"

# Matches the server's limit for `provider.error.type` and `provider.error.code`; a longer
# value would be dropped at ingest, so it is not sent.
MAX_ERROR_CHARS = 64
# A wait is a short number; anything longer is not one worth parsing.
_MAX_RETRY_AFTER_CHARS = 32


def record_response_details(span: Span, error: BaseException) -> None:
    """Record the provider error fields of ``error`` on ``span``. Never raises.

    Only an exception with a ``response`` that has both ``headers`` and ``status_code`` (the
    official SDKs' ``APIStatusError``) records anything; ``error.request`` is never read.
    """
    try:
        response = getattr(error, "response", None)
        if response is None:
            return
        if not (hasattr(response, "headers") and hasattr(response, "status_code")):
            return
        gateway_code = gateway_error_code(response.headers)
        if gateway_code is not None:
            span.set_attribute(GATEWAY_ERROR_CODE, gateway_code)
            return
        _record_headers(span, response.headers)
        _record_error_kind(span, error)
    except Exception:
        logger.debug("Failed to record provider error details", exc_info=True)


def provider_request_id(error: BaseException) -> str | None:
    """The provider's request id for ``error``, or None. Never raises.

    The gateway's ``x-upstream-request-id`` when the response carries one, else the
    ``error.request_id`` the SDKs read from ``x-request-id``; None on a gateway-made response,
    whose every header is the gateway's.
    """
    try:
        response = getattr(error, "response", None)
        headers = getattr(response, "headers", None)
        if gateway_error_code(headers) is not None:
            return None
        upstream = (
            _header(headers, _UPSTREAM_REQUEST_ID_HEADER) if isinstance(headers, Mapping) else None
        )
        request_id = upstream if isinstance(upstream, str) else getattr(error, "request_id", None)
        return request_id if isinstance(request_id, str) else None
    except Exception:
        logger.debug("Failed to read the provider request id", exc_info=True)
        return None


def gateway_error_code(headers: Any) -> str | None:
    """The gateway's own error code from ``X-Spanlight-Code``, or None.

    The value is stripped of surrounding whitespace and then must match exactly: one of
    ``GATEWAY_CODES`` or a ``FAULT_<SCENARIO>`` code. Any other value, including ``UPSTREAM_ERROR``
    and a lower-case spelling, is not a gateway refusal and is ignored.
    """
    if not isinstance(headers, Mapping):
        return None
    value = _header(headers, _GATEWAY_CODE_HEADER)
    if not isinstance(value, str):
        return None
    code = value.strip()
    if code in GATEWAY_CODES or _FAULT_CODE.fullmatch(code):
        return code
    return None


def _header(headers: Mapping[Any, Any], name: str) -> Any:
    """``headers[name]`` with the name matched case-insensitively (httpx or a plain mapping)."""
    for key, value in headers.items():
        if isinstance(key, str) and key.lower() == name:
            return value
    return None


def _record_headers(span: Span, headers: Any) -> None:
    if not isinstance(headers, Mapping):
        return
    snapshot = snapshot_headers(headers, failed=True)
    if snapshot:
        span.set_attribute(RATELIMIT, snapshot)
    retry_after = parse_retry_after(snapshot)
    if retry_after is not None:
        span.set_attribute(RETRY_AFTER_S, retry_after)


def parse_retry_after(headers: Mapping[str, str]) -> float | None:
    """The provider's wait in seconds, the gateway's rule (``upstream_io.parse_retry_after``).

    ``retry-after-ms`` divided by 1000 when present and a valid number, else ``retry-after``
    (an integer or a decimal). An HTTP date, a negative or non-finite number, or junk is ``None``.
    """
    milliseconds = _non_negative_number(headers.get("retry-after-ms"))
    if milliseconds is not None:
        return milliseconds / 1000
    return _non_negative_number(headers.get("retry-after"))


def _non_negative_number(value: str | None) -> float | None:
    if value is None or len(value) > _MAX_RETRY_AFTER_CHARS:
        return None
    try:
        number = float(value.strip())
    except ValueError:
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return number


def _record_error_kind(span: Span, error: BaseException) -> None:
    error_object = _error_object(getattr(error, "body", None))
    error_type = _bounded(error_object.get("type")) or _bounded(getattr(error, "type", None))
    details = error_object.get("details")
    detail_code = details.get("error_code") if isinstance(details, Mapping) else None
    error_code = (
        _bounded(error_object.get("code"))
        or _bounded(detail_code)
        or _bounded(getattr(error, "code", None))
    )
    if error_type is not None:
        span.set_attribute(ERROR_TYPE, error_type)
    if error_code is not None:
        span.set_attribute(ERROR_CODE, error_code)


def _error_object(body: Any) -> Mapping[str, Any]:
    """The provider's error object: ``body["error"]`` (Anthropic) or ``body`` itself (OpenAI)."""
    if not isinstance(body, Mapping):
        return {}
    inner = body.get("error")
    return inner if isinstance(inner, Mapping) else body


def _bounded(value: Any) -> str | None:
    if isinstance(value, str) and value and len(value) <= MAX_ERROR_CHARS:
        return value
    return None
