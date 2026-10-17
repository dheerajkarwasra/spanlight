"""`classify_error`'s gateway rules (pure): the code table, the last status attribute, and the
Anthropic self-set spend limit."""

from typing import Any

import pytest

from app.db.models import SpanStatus
from app.ingest.error_class import (
    GATEWAY_CODE_CLASSES,
    STATUS_ATTRIBUTES,
    ErrorClass,
    classify_error,
)


def _failed(message: str | None, attributes: dict[str, Any]) -> ErrorClass | None:
    return classify_error(SpanStatus.ERROR, message, attributes)


# (spanlight_code, the status the gateway answers with, expected class)
SPEC_ROWS = [
    ("INVALID_REQUEST", 400, ErrorClass.CLIENT),
    ("NO_COMPATIBLE_TARGET", 400, ErrorClass.CLIENT),
    ("MODEL_NOT_ALLOWED", 404, ErrorClass.CLIENT),  # the OpenAI envelope's `model_not_found`
    ("PAYLOAD_TOO_LARGE", 413, ErrorClass.CLIENT),
    ("NOT_FOUND", 404, ErrorClass.CLIENT),
    ("METHOD_NOT_ALLOWED", 405, ErrorClass.CLIENT),
    ("UNAUTHORIZED", 401, ErrorClass.AUTH),
    ("RATE_LIMITED", 429, ErrorClass.RATE_LIMIT),
    ("BUDGET_EXCEEDED", 402, ErrorClass.GATEWAY),
    ("UPSTREAM_TIMEOUT", 504, ErrorClass.TIMEOUT),
    ("UPSTREAM_UNREACHABLE", 502, ErrorClass.NETWORK),
    ("UPSTREAM_BLOCKED", 502, ErrorClass.GATEWAY),
    ("INTERNAL_ERROR", 500, ErrorClass.GATEWAY),
    ("SERVICE_UNAVAILABLE", 503, ErrorClass.GATEWAY),
]


@pytest.mark.parametrize(("code", "status", "expected"), SPEC_ROWS, ids=[r[0] for r in SPEC_ROWS])
def test_gateway_code_decides_the_class(code: str, status: int, expected: ErrorClass) -> None:
    attributes = {"spanlight.gateway.error_code": code, "spanlight.gateway.status": status}

    # A message that would point elsewhere: the code table wins and nothing else runs.
    assert _failed("connection reset by peer: request timed out", attributes) == expected


def test_gateway_code_table_is_the_spec_table() -> None:
    assert dict(GATEWAY_CODE_CLASSES) == {code: expected for code, _, expected in SPEC_ROWS}


def test_gateway_code_wins_over_an_upstream_status() -> None:
    attributes = {
        "spanlight.gateway.error_code": "INTERNAL_ERROR",
        "spanlight.gateway.upstream_status": 429,
    }

    assert _failed(None, attributes) == ErrorClass.GATEWAY


def test_gateway_code_does_not_class_a_successful_span() -> None:
    attributes = {"spanlight.gateway.error_code": "INTERNAL_ERROR"}

    assert classify_error(SpanStatus.OK, None, attributes) is None


def test_unknown_gateway_code_falls_through_to_status() -> None:
    new_code = {"spanlight.gateway.error_code": "SOMETHING_NEW", "spanlight.gateway.status": 500}
    fault = {
        "spanlight.gateway.error_code": "FAULT_RATE_LIMITED",
        "spanlight.gateway.upstream_status": 429,
    }

    assert _failed(None, new_code) == ErrorClass.PROVIDER_5XX
    assert _failed("Rate limit reached for requests.", fault) == ErrorClass.RATE_LIMIT


@pytest.mark.parametrize("code", [["RATE_LIMITED"], {"x": 1}, 42, True, None, ""])
def test_non_string_gateway_code_falls_through(code: Any) -> None:
    attributes = {"spanlight.gateway.error_code": code, "spanlight.gateway.status": 500}

    assert _failed(None, attributes) == ErrorClass.PROVIDER_5XX


def test_gateway_status_is_the_last_status_attribute() -> None:
    assert STATUS_ATTRIBUTES == (
        "spanlight.gateway.upstream_status",
        "http.response.status_code",
        "http.status_code",
        "error.status",
        "spanlight.gateway.status",
    )
    assert _failed(None, {"spanlight.gateway.status": 429}) == ErrorClass.RATE_LIMIT
    both = {"spanlight.gateway.status": 429, "error.status": 500}
    assert _failed(None, both) == ErrorClass.PROVIDER_5XX


@pytest.mark.parametrize(
    "message",
    [
        "You have reached your specified API usage limits. You will regain access on 2026-11-01.",
        "You have reached your specified workspace API usage limits. You will regain access soon.",
    ],
)
def test_anthropic_self_set_limit_400_is_rate_limit(message: str) -> None:
    assert _failed(message, {"http.status_code": 400}) == ErrorClass.RATE_LIMIT


def test_spend_limit_wording_elsewhere_in_a_400_stays_client() -> None:
    # Only Anthropic's documented prefix refines; a 400 that merely mentions it is the caller's.
    message = "Bad field: You have reached your specified API usage limits"

    assert _failed(message, {"http.status_code": 400}) == ErrorClass.CLIENT
    # And only a 400: the same text on a 404 is still a client error.
    prefix = "You have reached your specified API usage limits."
    assert _failed(prefix, {"http.status_code": 404}) == ErrorClass.CLIENT


def test_529_stays_provider_5xx() -> None:
    assert _failed("Overloaded", {"spanlight.gateway.upstream_status": 529}) == (
        ErrorClass.PROVIDER_5XX
    )


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [
        # Reused from tests/ingest/test_ingest_db_limits.py
        # (test_ingest_bad_status_attribute_does_not_fail_batch).
        pytest.param("429", ErrorClass.RATE_LIMIT, id="string"),
        pytest.param(429.0, ErrorClass.RATE_LIMIT, id="float"),
        pytest.param(True, ErrorClass.UNKNOWN, id="bool"),
    ],
)
def test_existing_classes_unchanged(status_code: Any, expected: ErrorClass) -> None:
    assert _failed(None, {"http.status_code": status_code}) == expected


def test_existing_refinements_unchanged() -> None:
    too_long = "prompt is too long: 250000 tokens > 200000 maximum"

    assert _failed(too_long, {"http.status_code": 400}) == ErrorClass.CONTEXT_LENGTH
    assert _failed("blocked by content filter", {"http.status_code": 400}) == (
        ErrorClass.CONTENT_FILTER
    )
    assert _failed("Invalid parameter", {"http.status_code": 400}) == ErrorClass.CLIENT
