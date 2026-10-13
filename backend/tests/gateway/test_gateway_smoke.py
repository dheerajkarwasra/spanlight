"""The gateway fixtures work end to end: one call in, the provider's answer out, one span stored."""

import json
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.observability import GATEWAY_REQUESTS
from app.gateway.context import GatewayRequest
from app.gateway.execute import execute
from tests.gateway_fixtures import (
    ANTHROPIC_API_KEY,
    OPENAI_API_KEY,
    GatewayContextFactory,
    GatewayWorkspace,
)
from tests.gateway_support import (
    FakeProvider,
    fixture_text,
    metric_delta,
    read_gateway_span,
    sse_frames,
)

CHAT_BODY = {"model": "gpt-4.1-mini", "messages": [{"role": "user", "content": "Hello"}]}
# `chat.json` usage: 19 input tokens (8 of them cached) and 9 output tokens, at the seed
# gpt-4.1-mini rates (0.40 input, 0.10 cached input, 1.60 output per million tokens).
CHAT_COST = Decimal("0.00001960")
ANTHROPIC_MESSAGE = {
    "id": "msg_test_smoke0001",
    "type": "message",
    "role": "assistant",
    "model": "claude-haiku-4-5-20251001",
    "content": [{"type": "text", "text": "Hello!"}],
    "stop_reason": "end_turn",
    "stop_sequence": None,
    "usage": {
        "input_tokens": 12,
        "output_tokens": 6,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
    },
}


async def test_a_chat_completion_passes_through_and_becomes_one_priced_span(
    gateway_workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    answer = fixture_text("openai/chat.json")
    fake_provider.reply_json(answer.encode())
    served = metric_delta(GATEWAY_REQUESTS, surface="chat_completions", outcome="ok")

    response = await gateway_workspace.gw.post("/gw/v1/chat/completions", json=CHAT_BODY)

    assert response.status_code == 200, response.text
    assert response.content == answer.encode()
    assert served.value == 1
    [sent] = fake_provider.requests
    assert sent.url == "https://api.openai.com/v1/chat/completions"
    assert sent.headers["authorization"] == f"Bearer {OPENAI_API_KEY}"
    assert sent.json == CHAT_BODY
    assert fake_provider.open_streams == 0
    span = await read_gateway_span(session_factory, gateway_workspace.project_id)
    assert (span["kind"], span["status"]) == ("llm", "ok")
    assert (span["provider"], span["model"]) == ("openai", "gpt-4.1-mini-2025-04-14")
    assert (span["input_tokens"], span["output_tokens"], span["cached_tokens"]) == (19, 9, 8)
    assert span["cost_usd"] == CHAT_COST
    assert span["source_key_id"] == gateway_workspace.gateway_key_id


async def test_a_streamed_chat_completion_reaches_the_client_whole_and_becomes_one_ok_span(
    gateway_workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    sse = fixture_text("openai/chat_stream.sse")
    fake_provider.reply_stream(sse_frames(sse))
    # The client asks for usage itself, so the gateway forwards the usage chunk unchanged.
    body = {**CHAT_BODY, "stream": True, "stream_options": {"include_usage": True}}

    response = await gateway_workspace.gw.post("/gw/v1/chat/completions", json=body)

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.content == b"".join(sse_frames(sse))
    assert (fake_provider.streams_opened, fake_provider.open_streams) == (1, 0)
    span = await read_gateway_span(session_factory, gateway_workspace.project_id)
    assert (span["kind"], span["status"]) == ("llm", "ok")
    assert (span["input_tokens"], span["output_tokens"], span["cached_tokens"]) == (19, 9, 8)
    assert span["cost_usd"] == CHAT_COST
    assert span["time_to_first_token_ms"] is not None


async def test_an_anthropic_message_becomes_one_span_with_usage(
    gateway_workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    fake_provider.reply_json(ANTHROPIC_MESSAGE)
    body = {
        "model": "claude-haiku-4-5",
        "max_tokens": 64,
        "messages": [{"role": "user", "content": "Hello"}],
    }

    response = await gateway_workspace.gw.post("/gw/v1/messages", json=body)

    assert response.status_code == 200, response.text
    assert response.json() == ANTHROPIC_MESSAGE
    [sent] = fake_provider.requests
    assert sent.url == "https://api.anthropic.com/v1/messages"
    assert sent.headers["x-api-key"] == ANTHROPIC_API_KEY
    span = await read_gateway_span(session_factory, gateway_workspace.project_id)
    assert (span["status"], span["provider"]) == ("ok", "anthropic")
    assert (span["input_tokens"], span["output_tokens"]) == (12, 6)
    assert span["cost_usd"] is not None


async def test_a_direct_execute_call_writes_one_span(
    gateway_context: GatewayContextFactory,
    gateway_workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    answer = fixture_text("openai/chat.json")
    fake_provider.reply_json(answer.encode())
    context = await gateway_context(credential="openai")

    result = await execute(
        GatewayRequest(surface="chat_completions", body=CHAT_BODY, stream=False), context
    )

    assert result.status == 200
    assert json.loads(result.body or b"") == json.loads(answer)
    span = await read_gateway_span(session_factory, gateway_workspace.project_id)
    assert (span["span_id"], span["status"]) == (result.span_id, "ok")
    assert (span["input_tokens"], span["output_tokens"]) == (19, 9)
    assert span["source_key_id"] is None
