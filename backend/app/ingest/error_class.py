"""Which kind of failure a span is. Pure: no database, no I/O.

`classify_error` gives every failed span one `ErrorClass`, so the explorer can filter on it and
the insight detectors can count failures by cause instead of matching messages themselves.

The rules, in order:

1. A span that did not fail has no class (`None`).
2. A gateway-made error decides first: when `spanlight.gateway.error_code` is one of the codes in
   `GATEWAY_CODE_CLASSES`, the class comes from that table and no other rule runs. An unknown
   code (a lab fault's `FAULT_<SCENARIO>`, or a code added later) falls through.
3. A numeric status decides next. It is read from `STATUS_ATTRIBUTES` in order, the first one
   present (`spanlight.gateway.status` last, so a gateway-made status never hides the upstream
   one), else from `error.type` when it is 1-3 ASCII digits (GenAI semconv puts the provider's
   status code there): 401 and 403 are `auth`, 429 is `rate_limit`, 408, 499 and 504 are
   `timeout`, 413 is `context_length`, any 5xx (529 included) is `provider_5xx`, and any other
   4xx is `client`
   unless the text (rule 4) is more specific: it says the context was too long or the content was
   filtered, or it is a 400 carrying Anthropic's self-set spend limit text, which is
   `rate_limit`. That text counts when the message starts with it (the provider's own message) or
   when the span's `provider.error.type` is `invalid_request_error` (the gateway's
   `"400 invalid_request_error: …"` and the SDK's `str(APIStatusError)` put a prefix before it).
4. Otherwise the text decides: the first group of patterns it contains, case-insensitively, in
   the order of `_MESSAGE_PATTERNS`. The text of `error.type` (an exception class name such as
   `RateLimitError`, which OpenInference, Traceloop and LiteLLM export) is matched before the
   status message, since it is the more precise of the two; a `client` from `error.type`
   (`BadRequestError` names no cause) yields to the message.
5. Anything else is `unknown`.
"""

import enum
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from app.db.models import SpanStatus


class ErrorClass(enum.StrEnum):
    AUTH = "auth"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    CONTEXT_LENGTH = "context_length"
    CONTENT_FILTER = "content_filter"
    PROVIDER_5XX = "provider_5xx"
    NETWORK = "network"
    CLIENT = "client"
    UNKNOWN = "unknown"
    GATEWAY = "gateway"


STATUS_ATTRIBUTES = (
    "spanlight.gateway.upstream_status",
    "http.response.status_code",
    "http.status_code",
    "error.status",
    "spanlight.gateway.status",
)

GATEWAY_ERROR_CODE_ATTRIBUTE = "spanlight.gateway.error_code"
ERROR_TYPE_ATTRIBUTE = "error.type"

# The class of every error the gateway makes itself, by its `spanlight_code`
# (`app/gateway/errors.py`). Spanlight's own refusals and failures are `gateway`, so they are never
# charged to the provider.
GATEWAY_CODE_CLASSES: Mapping[str, ErrorClass] = MappingProxyType(
    {
        "INVALID_REQUEST": ErrorClass.CLIENT,
        "NO_COMPATIBLE_TARGET": ErrorClass.CLIENT,
        "MODEL_NOT_ALLOWED": ErrorClass.CLIENT,
        "PAYLOAD_TOO_LARGE": ErrorClass.CLIENT,
        "NOT_FOUND": ErrorClass.CLIENT,
        "METHOD_NOT_ALLOWED": ErrorClass.CLIENT,
        "UNAUTHORIZED": ErrorClass.AUTH,
        "RATE_LIMITED": ErrorClass.RATE_LIMIT,
        "BUDGET_EXCEEDED": ErrorClass.GATEWAY,
        "UPSTREAM_BLOCKED": ErrorClass.GATEWAY,
        "INTERNAL_ERROR": ErrorClass.GATEWAY,
        "SERVICE_UNAVAILABLE": ErrorClass.GATEWAY,
        "UPSTREAM_TIMEOUT": ErrorClass.TIMEOUT,
        "UPSTREAM_UNREACHABLE": ErrorClass.NETWORK,
    }
)

_STATUS_CLASSES: dict[int, ErrorClass] = {
    401: ErrorClass.AUTH,
    403: ErrorClass.AUTH,
    429: ErrorClass.RATE_LIMIT,
    408: ErrorClass.TIMEOUT,
    499: ErrorClass.TIMEOUT,
    504: ErrorClass.TIMEOUT,
    413: ErrorClass.CONTEXT_LENGTH,
}

# First match wins, so the order matters: "rate limit exceeded: request timed out" is a rate
# limit, and a disconnect or abort is a timeout before it is a network error. SDK exception class
# names (`ratelimiterror`, `permissiondenied`, ...) sit in their groups; `AuthenticationError`,
# `APITimeoutError` and `APIConnectionError` already match by substring. `badrequesterror` is the
# last group so it never shadows a more specific text in the same message.
_MESSAGE_PATTERNS: tuple[tuple[ErrorClass, tuple[str, ...]], ...] = (
    (
        ErrorClass.AUTH,
        (
            "unauthorized",
            "invalid api key",
            "authentication",
            "permission denied",
            "permissiondenied",
            "forbidden",
        ),
    ),
    (ErrorClass.RATE_LIMIT, ("rate limit", "too many requests", "quota", "ratelimiterror")),
    (
        ErrorClass.TIMEOUT,
        ("timed out", "timeout", "deadline exceeded", "client disconnected", "aborted"),
    ),
    (
        ErrorClass.CONTEXT_LENGTH,
        (
            "context length",
            "context window",
            "maximum context",
            "too many tokens",
            "prompt is too long",
            "contextwindowexceeded",
        ),
    ),
    (ErrorClass.CONTENT_FILTER, ("content filter", "content_policy", "safety", "refusal")),
    (ErrorClass.NETWORK, ("connection", "dns", "ssl", "reset by peer", "econnrefused")),
    (
        ErrorClass.PROVIDER_5XX,
        ("overloaded", "internal server error", "service unavailable", "bad gateway", "upstream"),
    ),
    (ErrorClass.CLIENT, ("badrequesterror",)),
)

# A 4xx whose message names one of these is that class rather than `client`.
_REFINES_CLIENT = frozenset({ErrorClass.CONTEXT_LENGTH, ErrorClass.CONTENT_FILTER})

# Anthropic's self-set spend limit is documented as a 400 `invalid_request_error` whose message
# starts with one of these; it is a rate limit (kind `spend_cap`), not the caller's mistake.
_SPEND_LIMIT_400_PHRASES = (
    "you have reached your specified api usage limits",
    "you have reached your specified workspace api usage limits",
)
_PROVIDER_ERROR_TYPE_ATTRIBUTE = "provider.error.type"
_SPEND_LIMIT_400_ERROR_TYPE = "invalid_request_error"


def classify_error(
    status: SpanStatus, status_message: str | None, attributes: Mapping[str, Any]
) -> ErrorClass | None:
    """The span's error class, or None when the span did not fail (see the module docstring)."""
    if status != SpanStatus.ERROR:
        return None
    gateway_code = attributes.get(GATEWAY_ERROR_CODE_ATTRIBUTE)
    if isinstance(gateway_code, str) and gateway_code in GATEWAY_CODE_CLASSES:
        return GATEWAY_CODE_CLASSES[gateway_code]
    error_type = attributes.get(ERROR_TYPE_ATTRIBUTE)
    from_type = _class_from_message(error_type if isinstance(error_type, str) else None)
    from_message = (
        from_type
        if from_type not in (None, ErrorClass.CLIENT)
        else _class_from_message(status_message) or from_type
    )
    code = numeric_status(attributes)
    if code is not None:
        from_status = _class_from_status(code)
        if from_status == ErrorClass.CLIENT:
            refined = _refine_client(code, status_message, from_message, attributes)
            if refined is not None:
                return refined
        if from_status is not None:
            return from_status
    return from_message or ErrorClass.UNKNOWN


def numeric_status(attributes: Mapping[str, Any]) -> int | None:
    """The first status attribute holding a whole number (or a string of 1-3 ASCII digits),
    else `error.type` when it is one."""
    for key in (*STATUS_ATTRIBUTES, ERROR_TYPE_ATTRIBUTE):
        value = attributes.get(key)
        if isinstance(value, bool) or value is None:
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, str):
            digits = value.strip()
            # ASCII only and at most 3 digits: str.isdigit() also accepts "²" and "①", which
            # int() rejects, and a client value must never fail the ingest batch.
            if digits.isascii() and digits.isdigit() and len(digits) <= 3:
                return int(digits)
    return None


def _refine_client(
    code: int,
    message: str | None,
    from_message: ErrorClass | None,
    attributes: Mapping[str, Any],
) -> ErrorClass | None:
    """The more specific class of a 4xx `client` error, or None when it stays `client`."""
    if from_message in _REFINES_CLIENT:
        return from_message
    if code == 400 and message and _is_spend_limit_400(message, attributes):
        return ErrorClass.RATE_LIMIT
    return None


def _is_spend_limit_400(message: str, attributes: Mapping[str, Any]) -> bool:
    """A 400's message carries the self-set spend limit text: as the message itself, or after a
    prefix when the provider's error type says it is that documented 400."""
    lowered = message.lstrip().lower()
    if lowered.startswith(_SPEND_LIMIT_400_PHRASES):
        return True
    error_type = attributes.get(_PROVIDER_ERROR_TYPE_ATTRIBUTE)
    return error_type == _SPEND_LIMIT_400_ERROR_TYPE and any(
        phrase in lowered for phrase in _SPEND_LIMIT_400_PHRASES
    )


def _class_from_status(code: int) -> ErrorClass | None:
    known = _STATUS_CLASSES.get(code)
    if known is not None:
        return known
    if 500 <= code <= 599:
        return ErrorClass.PROVIDER_5XX
    if 400 <= code <= 499:
        return ErrorClass.CLIENT
    return None  # not an error status; the message decides


def _class_from_message(message: str | None) -> ErrorClass | None:
    if not message:
        return None
    lowered = message.lower()
    for error_class, patterns in _MESSAGE_PATTERNS:
        if any(pattern in lowered for pattern in patterns):
            return error_class
    return None
