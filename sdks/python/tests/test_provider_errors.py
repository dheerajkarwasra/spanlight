"""Failed provider calls record the provider's error type and code, retry-after and headers.

The real ``openai`` and ``anthropic`` SDKs run on a mocked HTTP transport, so the exceptions
the wrappers see are the ones the provider SDKs raise in production.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, ClassVar

import anthropic
import openai
import pytest
from provider_mocks import (
    OPENAI_RATE_LIMIT_ERROR,
    MockProvider,
    provider_httpx,
)
from support import flushed_spans

import spanlight
from spanlight.integrations._common import record_provider_error
from spanlight.integrations._provider_errors import record_response_details

OPENAI_KEY = "sk-proj-test-secret-key-0123456789"
ANTHROPIC_KEY = "sk-ant-test-secret-key-0123456789"
MESSAGES = [{"role": "user", "content": "How long do refunds take?"}]
KELVIN_SIGN = "\N{KELVIN SIGN}"
NEW_ATTRIBUTES = (
    "provider.ratelimit",
    "provider.retry_after_s",
    "provider.error.type",
    "provider.error.code",
)

ANTHROPIC_SPEND_CAP = {
    "type": "error",
    "error": {
        "type": "rate_limit_error",
        "message": "You have reached your API usage limits. Please try again later.",
        "details": {"error_code": "enforced_spend_limit_reached"},
    },
}


def error_response(
    body: dict[str, Any] | str, status: int, headers: dict[str, str]
) -> Callable[[Any], Any]:
    """Answer every request with ``status``, ``body`` and ``headers``."""

    def respond(request: Any) -> Any:
        content = body if isinstance(body, str) else json.dumps(body)
        return provider_httpx.Response(
            status,
            content=content.encode(),
            headers={"content-type": "application/json", **headers},
        )

    return respond


def openai_client(provider: MockProvider, tracer: spanlight.Spanlight) -> openai.OpenAI:
    client = openai.OpenAI(api_key=OPENAI_KEY, http_client=provider.sync_client(), max_retries=0)
    return spanlight.wrap_openai(client, spanlight=tracer)


def anthropic_client(provider: MockProvider, tracer: spanlight.Spanlight) -> anthropic.Anthropic:
    client = anthropic.Anthropic(
        api_key=ANTHROPIC_KEY, http_client=provider.sync_client(), max_retries=0
    )
    return spanlight.wrap_anthropic(client, spanlight=tracer)


def failed_openai_attributes(
    tracer: spanlight.Spanlight, ingest: Any, headers: dict[str, str], status: int = 429
) -> dict[str, Any]:
    provider = MockProvider(error_response(OPENAI_RATE_LIMIT_ERROR, status, headers))
    with pytest.raises(openai.APIStatusError):
        openai_client(provider, tracer).chat.completions.create(
            model="gpt-4o-mini", messages=MESSAGES
        )
    span = flushed_spans(tracer, ingest)[0]
    assert span["status"] == "error"
    attributes: dict[str, Any] = span["attributes"]
    return attributes


def test_openai_429_records_headers_type_code_and_retry_after_ms(tracer, ingest):
    attributes = failed_openai_attributes(
        tracer,
        ingest,
        {
            "retry-after": "2",
            "retry-after-ms": "1500",
            "x-ratelimit-limit-tokens": "200000",
            "x-ratelimit-remaining-tokens": "0",
            "x-ratelimit-reset-tokens": "1.5s",
            "x-request-id": "req_openai_429",
            "openai-processing-ms": "12",
        },
    )

    assert attributes["provider.ratelimit"] == {
        "retry-after": "2",
        "retry-after-ms": "1500",
        "x-ratelimit-limit-tokens": "200000",
        "x-ratelimit-remaining-tokens": "0",
        "x-ratelimit-reset-tokens": "1.5s",
    }
    assert attributes["provider.retry_after_s"] == 1.5
    assert attributes["provider.error.type"] == "tokens"
    assert attributes["provider.error.code"] == "rate_limit_exceeded"
    assert attributes["http.status_code"] == 429
    assert attributes["provider.request_id"] == "req_openai_429"


def test_retry_after_seconds_are_used_without_retry_after_ms(tracer, ingest):
    attributes = failed_openai_attributes(tracer, ingest, {"retry-after": "7"})

    assert attributes["provider.retry_after_s"] == 7.0
    assert isinstance(attributes["provider.retry_after_s"], float)


def test_anthropic_spend_cap_records_code_from_details(tracer, ingest):
    provider = MockProvider(
        error_response(
            ANTHROPIC_SPEND_CAP,
            429,
            {
                "request-id": "req_spend_cap",
                "anthropic-ratelimit-tokens-remaining": "0",
                "anthropic-organization-id": "org-uuid-not-a-limit",
            },
        )
    )

    with pytest.raises(anthropic.RateLimitError):
        anthropic_client(provider, tracer).messages.create(
            model="claude-haiku-4-5", max_tokens=64, messages=MESSAGES
        )

    attributes = flushed_spans(tracer, ingest)[0]["attributes"]
    assert attributes["provider.error.type"] == "rate_limit_error"
    assert attributes["provider.error.code"] == "enforced_spend_limit_reached"
    assert attributes["provider.ratelimit"] == {"anthropic-ratelimit-tokens-remaining": "0"}
    assert "provider.retry_after_s" not in attributes


def test_anthropic_error_without_body_uses_exception_type(tracer, ingest):
    # An exception whose body was not kept (or not JSON) still carries the SDK's parsed type.
    response = provider_httpx.Response(
        529,
        headers={"retry-after": "3"},
        request=provider_httpx.Request("POST", "https://api.anthropic.com/v1/messages"),
    )
    error = anthropic.APIStatusError("Overloaded", response=response, body=None)
    error.type = "overloaded_error"
    span = tracer.start_span("chat", kind="llm")

    record_provider_error(span, error)
    span.end()

    attributes = flushed_spans(tracer, ingest)[0]["attributes"]
    assert attributes["provider.error.type"] == "overloaded_error"
    assert "provider.error.code" not in attributes
    assert attributes["provider.retry_after_s"] == 3.0
    assert attributes["http.status_code"] == 529


def test_authorization_header_never_reaches_the_payload(tracer, ingest):
    secrets = {
        "authorization": "Bearer sk-live-should-never-leak",
        "x-api-key": "sk-ant-should-never-leak",
        "cookie": "session=should-never-leak",
        "set-cookie": "session=should-never-leak-either",
        "anthropic-workspace-id": "wrkspc_should_never_leak",
    }
    failed_openai_attributes(tracer, ingest, {**secrets, "retry-after": "1"})

    payload = json.dumps(ingest.spans)
    for name, value in secrets.items():
        assert name not in payload
        assert value not in payload


def test_junk_headers_are_dropped(tracer, ingest):
    junk = {f"x-junk-{index}": f"value-{index}" for index in range(40)}
    attributes = failed_openai_attributes(
        tracer,
        ingest,
        {
            **junk,
            "x-ratelimit-reset-requests": "1" * 65,  # too long
            "x-ratelimit-limit-requests": "500",
        },
    )

    assert attributes["provider.ratelimit"] == {"x-ratelimit-limit-requests": "500"}


def test_unusable_header_names_and_values_are_dropped(tracer, ingest):
    class FakeResponse:
        status_code = 429
        # KELVIN SIGN lowercases to an ASCII "k"; a non-ASCII name is skipped first.
        headers: ClassVar[dict[str, Any]] = {
            "X-Ratelimit-Limit-Requests": "500",
            f"{KELVIN_SIGN}-ratelimit-limit-tokens": "1",
            "x-ratelimit-remaining-requests": "tab\tvalue",
            "x-ratelimit-remaining-tokens": 0,
        }

    class FakeError(Exception):
        response = FakeResponse()
        body = None

    span = tracer.start_span("chat", kind="llm")
    record_response_details(span, FakeError("rate limited"))
    span.end()

    attributes = flushed_spans(tracer, ingest)[0]["attributes"]
    assert attributes["provider.ratelimit"] == {"x-ratelimit-limit-requests": "500"}


def test_no_allowlisted_header_sets_no_ratelimit_attribute(tracer, ingest):
    attributes = failed_openai_attributes(tracer, ingest, {"x-request-id": "req_1"})

    assert "provider.ratelimit" not in attributes
    assert "provider.retry_after_s" not in attributes
    assert attributes["provider.error.code"] == "rate_limit_exceeded"


def test_http_date_retry_after_sets_no_attribute(tracer, ingest):
    attributes = failed_openai_attributes(
        tracer, ingest, {"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"}
    )

    assert "provider.retry_after_s" not in attributes
    assert attributes["provider.ratelimit"] == {"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"}


@pytest.mark.parametrize("value", ["-1", "nan", "inf", "soon"])
def test_unusable_retry_after_sets_no_attribute(tracer, ingest, value):
    attributes = failed_openai_attributes(tracer, ingest, {"retry-after-ms": value})

    assert "provider.retry_after_s" not in attributes


def test_connection_error_sets_nothing_new(tracer, ingest):
    def refuse(request: Any) -> Any:
        raise provider_httpx.ConnectError("connection refused", request=request)

    with pytest.raises(openai.APIConnectionError):
        openai_client(MockProvider(refuse), tracer).chat.completions.create(
            model="gpt-4o-mini", messages=MESSAGES
        )

    span = flushed_spans(tracer, ingest)[0]
    assert span["status"] == "error"
    for name in (*NEW_ATTRIBUTES, "http.status_code"):
        assert name not in span["attributes"]


def test_stream_raising_on_first_read_gets_the_fields(tracer, ingest):
    provider = MockProvider(error_response(OPENAI_RATE_LIMIT_ERROR, 429, {"retry-after-ms": "250"}))

    with pytest.raises(openai.RateLimitError):
        stream = openai_client(provider, tracer).chat.completions.create(
            model="gpt-4o-mini", messages=MESSAGES, stream=True
        )
        next(iter(stream))

    attributes = flushed_spans(tracer, ingest)[0]["attributes"]
    assert attributes["provider.retry_after_s"] == 0.25
    assert attributes["provider.error.code"] == "rate_limit_exceeded"
    assert attributes["provider.ratelimit"] == {"retry-after-ms": "250"}


def test_anthropic_stream_helper_refused_on_enter_gets_the_fields(tracer, ingest):
    provider = MockProvider(
        error_response(ANTHROPIC_SPEND_CAP, 429, {"anthropic-ratelimit-requests-limit": "50"})
    )
    client = anthropic_client(provider, tracer)

    with (
        pytest.raises(anthropic.RateLimitError),
        client.messages.stream(model="claude-haiku-4-5", max_tokens=64, messages=MESSAGES) as s,
    ):
        s.until_done()

    attributes = flushed_spans(tracer, ingest)[0]["attributes"]
    assert attributes["provider.error.code"] == "enforced_spend_limit_reached"
    assert attributes["provider.ratelimit"] == {"anthropic-ratelimit-requests-limit": "50"}


def test_record_never_raises(tracer, ingest):
    class ExplodingResponse:
        status_code = 429

        @property
        def headers(self) -> Any:
            raise RuntimeError("headers unavailable")

    class FakeError(Exception):
        response = ExplodingResponse()

        @property
        def body(self) -> Any:
            raise RuntimeError("body unavailable")

    span = tracer.start_span("chat", kind="llm")
    record_response_details(span, FakeError("boom"))
    record_provider_error(span, FakeError("boom"))
    span.end()

    attributes = flushed_spans(tracer, ingest)[0]["attributes"]
    for name in NEW_ATTRIBUTES:
        assert name not in attributes
    assert attributes["error.type"] == "FakeError"


def test_exception_without_response_sets_nothing(tracer, ingest):
    class CodedError(Exception):
        type = "rate_limit_error"
        code = "rate_limit_exceeded"
        body: ClassVar[dict[str, Any]] = {"error": {"type": "rate_limit_error"}}

    span = tracer.start_span("chat", kind="llm")
    record_response_details(span, CodedError("no response"))
    span.end()

    attributes = flushed_spans(tracer, ingest)[0]["attributes"]
    for name in NEW_ATTRIBUTES:
        assert name not in attributes


def test_overlong_error_type_and_code_are_dropped(tracer, ingest):
    provider = MockProvider(
        error_response({"error": {"type": "t" * 65, "code": "c" * 65, "message": "bad"}}, 400, {})
    )

    with pytest.raises(openai.BadRequestError):
        openai_client(provider, tracer).chat.completions.create(
            model="gpt-4o-mini", messages=MESSAGES
        )

    attributes = flushed_spans(tracer, ingest)[0]["attributes"]
    assert "provider.error.type" not in attributes
    assert "provider.error.code" not in attributes
