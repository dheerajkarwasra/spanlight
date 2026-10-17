"""`classify_error`'s OTLP inputs (pure): `http.response.status_code`, `error.type` as a status
or as an exception class name, and their precedence against the status message."""

from typing import Any

import pytest

from app.db.models import SpanStatus
from app.ingest.error_class import STATUS_ATTRIBUTES, ErrorClass, classify_error, numeric_status


def _failed(message: str | None, attributes: dict[str, Any]) -> ErrorClass | None:
    return classify_error(SpanStatus.ERROR, message, attributes)


def test_status_attributes_are_the_program_list() -> None:
    assert STATUS_ATTRIBUTES == (
        "spanlight.gateway.upstream_status",
        "http.response.status_code",
        "http.status_code",
        "error.status",
        "spanlight.gateway.status",
    )


def test_http_response_status_code_is_read_second() -> None:
    attributes = {"http.response.status_code": 429, "http.status_code": 500}

    assert _failed(None, attributes) == ErrorClass.RATE_LIMIT
    assert _failed(None, {"spanlight.gateway.upstream_status": 503, **attributes}) == (
        ErrorClass.PROVIDER_5XX
    )


def test_error_type_digits_are_a_status() -> None:
    assert numeric_status({"error.type": "503"}) == 503
    assert _failed(None, {"error.type": "503"}) == ErrorClass.PROVIDER_5XX
    # Any status attribute is consulted before `error.type`.
    assert _failed(None, {"error.type": "503", "error.status": 429}) == ErrorClass.RATE_LIMIT
    # Not a status: four digits, a word, or a number that is not whole.
    assert numeric_status({"error.type": "5030"}) is None
    assert numeric_status({"error.type": "timeout"}) is None


@pytest.mark.parametrize(
    ("error_type", "expected"),
    [
        ("RateLimitError", ErrorClass.RATE_LIMIT),
        ("AuthenticationError", ErrorClass.AUTH),
        ("PermissionDenied", ErrorClass.AUTH),
        ("APITimeoutError", ErrorClass.TIMEOUT),
        ("APIConnectionError", ErrorClass.NETWORK),
        ("BadRequestError", ErrorClass.CLIENT),
        ("ContextWindowExceeded", ErrorClass.CONTEXT_LENGTH),
        ("openai.RateLimitError", ErrorClass.RATE_LIMIT),  # a dotted exception path
    ],
)
def test_error_type_class_name_without_message(error_type: str, expected: ErrorClass) -> None:
    assert _failed(None, {"error.type": error_type}) == expected


def test_bad_request_error_never_shadows_a_more_specific_text() -> None:
    assert _failed("BadRequestError: context window exceeded", {}) == ErrorClass.CONTEXT_LENGTH
    assert _failed("BadRequestError: content filter triggered", {}) == ErrorClass.CONTENT_FILTER
    assert _failed("BadRequestError: bad JSON", {}) == ErrorClass.CLIENT


def test_message_refines_a_bad_request_error_type() -> None:
    attributes = {"error.type": "BadRequestError"}
    context = "This model's maximum context length is 8192 tokens"

    assert _failed(context, attributes) == ErrorClass.CONTEXT_LENGTH
    assert _failed(context, {**attributes, "http.response.status_code": 400}) == (
        ErrorClass.CONTEXT_LENGTH
    )
    assert _failed("content filter triggered", attributes) == ErrorClass.CONTENT_FILTER
    assert _failed("bad JSON", attributes) == ErrorClass.CLIENT
    assert _failed(None, attributes) == ErrorClass.CLIENT


def test_error_type_text_beats_the_message() -> None:
    assert _failed("rate limit", {"error.type": "APITimeoutError"}) == ErrorClass.TIMEOUT


def test_status_decides_before_error_type_text() -> None:
    attributes = {"http.response.status_code": 401, "error.type": "RateLimitError"}

    assert _failed(None, attributes) == ErrorClass.AUTH


def test_error_type_text_refines_a_4xx() -> None:
    attributes = {"http.response.status_code": 400, "error.type": "ContextWindowExceeded"}

    assert _failed(None, attributes) == ErrorClass.CONTEXT_LENGTH


def test_ok_status_with_error_type_has_no_class() -> None:
    attributes = {"error.type": "RateLimitError", "http.response.status_code": 429}

    assert classify_error(SpanStatus.OK, "rate limit", attributes) is None
    assert classify_error(SpanStatus.UNSET, None, attributes) is None


def test_gateway_rule_still_runs_first() -> None:
    attributes = {
        "spanlight.gateway.error_code": "BUDGET_EXCEEDED",
        "error.type": "RateLimitError",
        "http.response.status_code": 429,
    }

    assert _failed("rate limit", attributes) == ErrorClass.GATEWAY


def test_unmatched_error_type_falls_back_to_the_message() -> None:
    assert _failed("connection reset by peer", {"error.type": "SomeError"}) == ErrorClass.NETWORK
    assert _failed(None, {"error.type": "SomeError"}) == ErrorClass.UNKNOWN
    assert _failed(None, {"error.type": 7}) == ErrorClass.UNKNOWN
