"""An error the Spanlight gateway made itself is marked gateway-made, not provider-made.

An app pointed at the gateway can be refused by the gateway (its gateway key's own rate limit,
a budget, a blocked upstream). The gateway answers in the provider's envelope and names the
refusal in an ``X-Spanlight-Code`` header. The SDK records that code as
``spanlight.gateway.error_code`` and leaves the ``provider.*`` error fields unset, so the
gateway's limiter is never mistaken for the provider's. ``UPSTREAM_ERROR`` marks a provider
error the gateway passed through unchanged; that one keeps its provider fields.

The real ``openai`` SDK runs on a mocked HTTP transport.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import openai
import pytest
from provider_mocks import OPENAI_RATE_LIMIT_ERROR, MockProvider, provider_httpx
from support import flushed_spans

import spanlight

GATEWAY_ERROR_CODE = "spanlight.gateway.error_code"
PROVIDER_FIELDS = (
    "provider.error.type",
    "provider.error.code",
    "provider.ratelimit",
    "provider.retry_after_s",
)
MESSAGES = [{"role": "user", "content": "How long do refunds take?"}]
OPENAI_KEY = "sk-proj-test-secret-key-0123456789"

# What the gateway renders for its own key limit in the OpenAI envelope (`errors.rate_limited`).
GATEWAY_RATE_LIMITED = {
    "error": {
        "message": "Rate limit reached for gateway key spl_gw_ab12: 60 requests per minute.",
        "type": "requests",
        "param": None,
        "code": "rate_limit_exceeded",
        "spanlight_code": "RATE_LIMITED",
    }
}


def error_response(
    body: dict[str, Any], status: int, headers: dict[str, str]
) -> Callable[..., Any]:
    """Answer every request with ``status``, ``body`` and ``headers``."""

    def respond(request: Any) -> Any:
        return provider_httpx.Response(
            status,
            content=json.dumps(body).encode(),
            headers={"content-type": "application/json", **headers},
        )

    return respond


def failed_span_attributes(
    tracer: spanlight.Spanlight,
    ingest: Any,
    body: dict[str, Any],
    status: int,
    headers: dict[str, str],
) -> dict[str, Any]:
    provider = MockProvider(error_response(body, status, headers))
    with pytest.raises(openai.APIStatusError):
        client = openai.OpenAI(
            api_key=OPENAI_KEY, http_client=provider.sync_client(), max_retries=0
        )
        spanlight.wrap_openai(client, spanlight=tracer).chat.completions.create(
            model="gpt-4o-mini", messages=MESSAGES
        )
    span = flushed_spans(tracer, ingest)[0]
    assert span["status"] == "error"
    attributes: dict[str, Any] = span["attributes"]
    return attributes


def test_gateway_rate_limited_refusal_sets_gateway_code_not_provider_fields(tracer, ingest):
    headers = {"x-spanlight-code": "RATE_LIMITED", "retry-after": "2", "x-request-id": "gw-1"}

    attributes = failed_span_attributes(tracer, ingest, GATEWAY_RATE_LIMITED, 429, headers)

    assert attributes[GATEWAY_ERROR_CODE] == "RATE_LIMITED"
    assert attributes["http.status_code"] == 429
    for name in PROVIDER_FIELDS:
        assert name not in attributes, name


@pytest.mark.parametrize(
    ("code", "status"),
    [
        ("BUDGET_EXCEEDED", 402),
        ("UPSTREAM_BLOCKED", 502),
        ("SERVICE_UNAVAILABLE", 503),
        ("FAULT_RATE_LIMITED", 429),
    ],
)
def test_other_gateway_codes_and_lab_faults_are_gateway_made(tracer, ingest, code, status):
    body = {"error": {"message": "Refused by Spanlight.", "type": "server_error", "code": None}}

    attributes = failed_span_attributes(tracer, ingest, body, status, {"x-spanlight-code": code})

    assert attributes[GATEWAY_ERROR_CODE] == code
    assert "provider.error.type" not in attributes


def test_upstream_error_code_header_is_ignored(tracer, ingest):
    headers = {
        "x-spanlight-code": "UPSTREAM_ERROR",
        "retry-after-ms": "1500",
        "x-ratelimit-remaining-tokens": "0",
    }

    attributes = failed_span_attributes(tracer, ingest, OPENAI_RATE_LIMIT_ERROR, 429, headers)

    assert GATEWAY_ERROR_CODE not in attributes
    assert attributes["provider.error.type"] == "tokens"
    assert attributes["provider.error.code"] == "rate_limit_exceeded"
    assert attributes["provider.retry_after_s"] == 1.5
    assert attributes["provider.ratelimit"]["x-ratelimit-remaining-tokens"] == "0"


@pytest.mark.parametrize(
    "value",
    ["SOMETHING_NEW", "", "FAULT_", "FAULT_rate limited", "FAULT_" + "X" * 64, "RATE_LIMITED,X"],
)
def test_unknown_code_value_is_ignored(tracer, ingest, value):
    headers = {"x-spanlight-code": value}

    attributes = failed_span_attributes(tracer, ingest, OPENAI_RATE_LIMIT_ERROR, 429, headers)

    assert GATEWAY_ERROR_CODE not in attributes
    assert attributes["provider.error.type"] == "tokens"
    assert attributes["provider.error.code"] == "rate_limit_exceeded"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("  RATE_LIMITED  ", "RATE_LIMITED"),
        ("RATE_LIMITED\t", "RATE_LIMITED"),
        ("rate_limited", None),
        ("Rate_Limited", None),
    ],
)
def test_lowercase_or_padded_header_value_handled(tracer, ingest, value, expected):
    # Surrounding whitespace is stripped; the code itself must match exactly, as the gateway
    # always sends it in upper case. A different spelling is not the gateway's.
    headers = {"X-Spanlight-Code": value}

    attributes = failed_span_attributes(tracer, ingest, GATEWAY_RATE_LIMITED, 429, headers)

    assert attributes.get(GATEWAY_ERROR_CODE) == expected
    assert ("provider.error.type" in attributes) is (expected is None)


@pytest.mark.parametrize(
    ("code", "body", "expected"),
    [
        ("RATE_LIMITED", GATEWAY_RATE_LIMITED, None),
        ("UPSTREAM_ERROR", OPENAI_RATE_LIMIT_ERROR, "req_provider_1"),
    ],
    ids=["gateway-made", "passed-through"],
)
def test_gateway_request_id_is_not_recorded_as_provider_request_id(
    tracer, ingest, code, body, expected
):
    # On a gateway-made answer, `x-request-id` is the gateway's own id, not the provider's.
    headers = {"x-spanlight-code": code, "x-request-id": "req_provider_1"}

    attributes = failed_span_attributes(tracer, ingest, body, 429, headers)

    assert attributes.get("provider.request_id") == expected


def test_passed_through_error_records_the_upstream_request_id(tracer, ingest):
    # On a passed-through provider error, `x-request-id` is the gateway's own id; the gateway
    # carries the provider's in `x-upstream-request-id`.
    headers = {
        "x-spanlight-code": "UPSTREAM_ERROR",
        "x-request-id": "gw-1",
        "x-upstream-request-id": "req_provider_1",
    }

    attributes = failed_span_attributes(tracer, ingest, OPENAI_RATE_LIMIT_ERROR, 429, headers)

    assert attributes["provider.request_id"] == "req_provider_1"
