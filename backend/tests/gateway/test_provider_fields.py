"""A gateway span keeps what the provider said: its error type and code, its request id and the
rate-limit headers it sent, for complete answers and streams alike.

The calls run through `execute` against the fake provider, and the span is read back from
Postgres, so the three provider columns are the ones ingest computed from the span's attributes.
A passed-through provider error still reaches the client byte for byte.
"""

import dataclasses
import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.gateway.context import GatewayRequest
from app.gateway.execute import execute
from tests.gateway.support import chat_frames, chat_request, read_to_end
from tests.gateway_fixtures import GatewayContextFactory, GatewayWorkspace
from tests.gateway_support import (
    MESSAGES_BODY,
    Fail,
    FakeProvider,
    fixture_text,
    read_gateway_span,
)

OPENAI_TOKENS_429 = {
    "error": {
        "message": "Rate limit reached for gpt-4.1-mini on tokens per min (TPM): Limit 30000.",
        "type": "tokens",
        "param": None,
        "code": "rate_limit_exceeded",
    }
}
ANTHROPIC_SPEND_CAP = {
    "type": "error",
    "error": {
        "type": "rate_limit_error",
        "message": "Your organization has reached its spend limit.",
        "details": {"error_code": "enforced_spend_limit_reached"},
    },
    "request_id": "req_011CSpendCap",
}
# What a provider sends with a successful answer: headroom, reset times, and a header the
# snapshot never keeps.
SUCCESS_HEADERS = {
    "x-request-id": "req_success_0001",
    "x-ratelimit-limit-requests": "500",
    "x-ratelimit-remaining-requests": "499",
    "x-ratelimit-limit-tokens": "30000",
    "x-ratelimit-remaining-tokens": "29972",
    "x-ratelimit-reset-requests": "120ms",
    "x-ratelimit-reset-tokens": "56ms",
    "openai-processing-ms": "321",
}
HEADROOM = {
    "x-ratelimit-limit-requests": "500",
    "x-ratelimit-remaining-requests": "499",
    "x-ratelimit-limit-tokens": "30000",
    "x-ratelimit-remaining-tokens": "29972",
}
PRIORITY_HEADERS = {
    "anthropic-priority-input-tokens-limit": "40000",
    "anthropic-priority-input-tokens-remaining": "0",
    "anthropic-priority-input-tokens-reset": "2026-10-16T10:00:30Z",
}


def _chat_answer() -> dict[str, Any]:
    answer: dict[str, Any] = json.loads(fixture_text("openai/chat.json"))
    return answer


async def _no_wait(seconds: float) -> None:
    """Retries in these tests do not wait."""


async def test_openai_429_body_fills_type_code_kind_and_headers(
    gateway_context: GatewayContextFactory,
    gateway_workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    body = json.dumps(OPENAI_TOKENS_429, indent=2).encode()  # not the gateway's own spacing
    headers = {"x-ratelimit-remaining-tokens": "0", "retry-after-ms": "2000"}
    fake_provider.reply_json(body, status=429, headers=headers)
    context = await gateway_context(credential="openai")

    result = await execute(chat_request(stream=False), context)

    # The client gets the provider's bytes: the provider's code on the span changes nothing
    # the client sees.
    assert (result.status, result.body) == (429, body)
    assert result.headers["X-Spanlight-Code"] == "UPSTREAM_ERROR"
    assert result.error is not None and result.error.openai is not None
    assert result.error.openai.code == "rate_limit_exceeded"
    span = await read_gateway_span(session_factory, gateway_workspace.project_id)
    assert (span["provider_error_type"], span["provider_error_code"]) == (
        "tokens",
        "rate_limit_exceeded",
    )
    assert span["rate_limit_kind"] == "tokens"
    attributes: dict[str, Any] = span["attributes"]
    assert attributes["provider.ratelimit"] == headers
    assert attributes["spanlight.gateway.retry_after_s"] == 2.0
    assert attributes["spanlight.gateway.status"] == 429
    assert "spanlight.gateway.error_code" not in attributes


async def test_anthropic_spend_cap_body_fills_code_from_details(
    gateway_context: GatewayContextFactory,
    gateway_workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    body = json.dumps(ANTHROPIC_SPEND_CAP).encode()
    fake_provider.reply_json(body, status=429, headers={"request-id": "req_011CSpendCap"})
    context = await gateway_context(credential="anthropic")
    request = GatewayRequest(surface="messages", body=MESSAGES_BODY, stream=False)

    result = await execute(request, context)

    assert (result.status, result.body) == (429, body)
    span = await read_gateway_span(session_factory, gateway_workspace.project_id)
    assert (span["provider_error_type"], span["provider_error_code"]) == (
        "rate_limit_error",
        "enforced_spend_limit_reached",
    )
    assert span["rate_limit_kind"] == "spend_cap"
    assert span["attributes"]["provider.request_id"] == "req_011CSpendCap"


async def test_success_span_keeps_limit_and_remaining_only(
    gateway_context: GatewayContextFactory,
    gateway_workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    fake_provider.reply_json(_chat_answer(), headers=SUCCESS_HEADERS)
    context = await gateway_context(credential="openai")

    result = await execute(chat_request(stream=False), context)

    assert result.status == 200
    span = await read_gateway_span(session_factory, gateway_workspace.project_id)
    attributes: dict[str, Any] = span["attributes"]
    assert attributes["provider.ratelimit"] == HEADROOM
    assert attributes["spanlight.gateway.status"] == 200
    assert (span["provider_error_type"], span["provider_error_code"]) == (None, None)
    assert span["rate_limit_kind"] is None
    for absent in ("provider.error.type", "provider.error.code", "spanlight.gateway.error_code"):
        assert absent not in attributes


async def test_request_id_from_upstream_header_is_stored(
    gateway_context: GatewayContextFactory,
    gateway_workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    fake_provider.reply_json(_chat_answer(), headers={"x-request-id": "req_upstream_42"})
    context = await gateway_context(credential="openai")

    result = await execute(chat_request(stream=False), context)

    assert result.headers["x-upstream-request-id"] == "req_upstream_42"
    span = await read_gateway_span(session_factory, gateway_workspace.project_id)
    assert span["attributes"]["provider.request_id"] == "req_upstream_42"
    assert "provider.ratelimit" not in span["attributes"]  # no rate-limit header was sent


async def test_anthropic_priority_headers_reach_the_client_and_the_snapshot(
    gateway_context: GatewayContextFactory,
    gateway_workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    overloaded = fixture_text("anthropic/error_529.json").encode()
    fake_provider.reply_json(overloaded, status=529, headers=PRIORITY_HEADERS)
    context = await gateway_context(credential="anthropic")
    request = GatewayRequest(surface="messages", body=MESSAGES_BODY, stream=False)

    result = await execute(request, context)

    assert {name: result.headers[name] for name in PRIORITY_HEADERS} == PRIORITY_HEADERS
    span = await read_gateway_span(session_factory, gateway_workspace.project_id)
    assert span["attributes"]["provider.ratelimit"] == PRIORITY_HEADERS


async def test_stream_span_carries_headers_and_attempt_log(
    gateway_context: GatewayContextFactory,
    gateway_workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    refused = {"x-request-id": "req_refused", "retry-after": "0"}
    fake_provider.reply_json(OPENAI_TOKENS_429, status=429, headers=refused)
    fake_provider.reply_stream(chat_frames(), headers=SUCCESS_HEADERS)
    # The stored route retries a 429 once on the same target.
    context = dataclasses.replace(await gateway_context(), sleep=_no_wait)

    result = await execute(chat_request(), context)
    assert result.stream is not None
    await read_to_end(result.stream)

    span = await read_gateway_span(session_factory, gateway_workspace.project_id)
    assert (span["status"], span["status_message"]) == ("ok", None)
    attributes: dict[str, Any] = span["attributes"]
    # The headers are the answer's, not the refused attempt's.
    assert attributes["provider.request_id"] == "req_success_0001"
    assert attributes["provider.ratelimit"] == HEADROOM
    log = attributes["spanlight.gateway.attempt_log"]
    assert [(entry["target"], entry["name"], entry["status"]) for entry in log] == [
        (0, "openai", 429),
        (0, "openai", 200),
    ]
    assert log[0]["retry_after_s"] == 0.0
    assert attributes["spanlight.gateway.attempts"] == 2


async def test_mid_stream_failure_keeps_the_response_headers(
    gateway_context: GatewayContextFactory,
    gateway_workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    frames = chat_frames()
    fake_provider.reply_stream([frames[0], frames[1], Fail()], headers=SUCCESS_HEADERS)
    context = await gateway_context(credential="openai")

    result = await execute(chat_request(), context)
    assert result.stream is not None
    await read_to_end(result.stream)

    span = await read_gateway_span(session_factory, gateway_workspace.project_id)
    assert span["status"] == "error"
    assert "stream_error" in span["status_message"]
    attributes: dict[str, Any] = span["attributes"]
    assert attributes["provider.request_id"] == "req_success_0001"
    # The provider answered 200; the stream broke afterwards, so this is not a refusal and
    # the snapshot keeps the headroom headers of that answer.
    assert attributes["provider.ratelimit"] == HEADROOM
    assert attributes["spanlight.gateway.status"] == 200
    assert [entry["status"] for entry in attributes["spanlight.gateway.attempt_log"]] == [200]
    assert span["rate_limit_kind"] is None


async def test_non_json_upstream_body_stores_no_provider_error_type(
    gateway_context: GatewayContextFactory,
    gateway_workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # A proxy's HTML error page names no error type: the provider said nothing, so the column
    # stays null rather than carrying the gateway's own status-message placeholder.
    body = b"<html>Bad Gateway</html>"
    fake_provider.reply_json(body, status=502, headers={"content-type": "text/html"})
    context = await gateway_context(credential="openai")

    result = await execute(chat_request(stream=False), context)

    assert (result.status, result.body) == (502, body)
    span = await read_gateway_span(session_factory, gateway_workspace.project_id)
    assert (span["provider_error_type"], span["provider_error_code"]) == (None, None)
    assert span["status_message"] == "502 upstream_error: The provider answered with HTTP 502."
    assert span["error_class"] == "provider_5xx"
    attributes: dict[str, Any] = span["attributes"]
    assert "provider.error.type" not in attributes
    assert "provider.error.code" not in attributes
