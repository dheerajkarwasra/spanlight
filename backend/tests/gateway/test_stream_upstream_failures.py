"""A provider stream that breaks, stalls or misbehaves after its first byte.

The client's stream then simply ends (no exception reaches it), the provider response is
closed, nothing is retried (the first byte has gone out) and the call is one span that says
why. Usage is what the provider reported before it stopped, else unknown.
"""

import json
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.gateway.execute import execute
from tests.gateway.interruptions import upstream_breaks, upstream_goes_quiet
from tests.gateway.support import (
    anthropic_frame,
    chat_frames,
    chat_request,
    messages_request,
    read_to_end,
)
from tests.gateway_fixtures import GatewayContextFactory, GatewayWorkspace
from tests.gateway_support import Fail, FakeProvider, logged, read_gateway_span

OVERSIZED_FRAME_BYTES = 1_100_000  # past `sse.MAX_FRAME_BYTES` (1 MB)
# `message_start` of a Messages stream: 100 input tokens, and the provider's placeholder of 1
# output token before any token is generated.
CUT_MESSAGE: dict[str, Any] = {
    "id": "msg_test_cut0001",
    "type": "message",
    "role": "assistant",
    "model": "claude-haiku-4-5-20251001",
    "content": [],
    "stop_reason": None,
    "usage": {
        "input_tokens": 100,
        "output_tokens": 1,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
    },
}


async def test_upstream_connection_error_mid_stream_ends_the_client_stream_cleanly(
    gateway_context: GatewayContextFactory,
    gateway_workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    context = await gateway_context()  # the stored route: a retry would be allowed before

    call = await upstream_breaks(fake_provider, context)  # the client's stream ends, no raise

    assert call.received == call.scripted
    assert (fake_provider.streams_opened, fake_provider.streams_closed) == (1, 1)
    assert len(fake_provider.requests) == 1  # no retry after the first byte
    span = await read_gateway_span(session_factory, gateway_workspace.project_id)
    assert span["status"] == "error"
    assert "stream_error: the provider connection failed (ReadError)" in span["status_message"]
    assert span["attributes"]["spanlight.gateway.attempts"] == 1
    assert span["attributes"]["spanlight.gateway.client_disconnected"] is False
    assert (span["input_tokens"], span["output_tokens"], span["cost_usd"]) == (None, None, None)
    assert span["output"] == {"role": "assistant", "content": "Hello"}


async def test_upstream_idle_timeout_mid_stream(
    gateway_context: GatewayContextFactory,
    gateway_workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
    short_idle_timeout: float,
) -> None:
    context = await gateway_context()

    call = await upstream_goes_quiet(fake_provider, context)  # quiet after the first chunk

    assert call.received == call.scripted[:1]
    assert (fake_provider.streams_opened, fake_provider.streams_closed) == (1, 1)
    span = await read_gateway_span(session_factory, gateway_workspace.project_id)  # drain ends
    assert span["status"] == "error"
    assert span["status_message"] == "200 stream_error: the provider sent nothing for 0.05 s"
    assert span["attributes"]["spanlight.gateway.attempts"] == 1
    assert (span["input_tokens"], span["cost_usd"]) == (None, None)


async def test_anthropic_stream_cut_after_message_start_keeps_input_and_unknown_output(
    gateway_context: GatewayContextFactory,
    gateway_workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    frames = [
        anthropic_frame("message_start", {"message": CUT_MESSAGE}),
        anthropic_frame("content_block_start", {"index": 0, "content_block": {"type": "text"}}),
        anthropic_frame(
            "content_block_delta", {"index": 0, "delta": {"type": "text_delta", "text": "Hel"}}
        ),
    ]
    fake_provider.reply_stream([*frames, Fail()])  # gone before `message_delta`
    context = await gateway_context(credential="anthropic")

    result = await execute(messages_request(), context)
    assert result.stream is not None
    received = await read_to_end(result.stream)

    assert received == frames
    assert fake_provider.open_streams == 0
    span = await read_gateway_span(session_factory, gateway_workspace.project_id)
    assert span["status"] == "error"
    assert "stream_error: the provider connection failed (ReadError)" in span["status_message"]
    # The input split came with `message_start`; the final output count never did, so it is
    # unknown (not the placeholder of 1) and the span has no cost, never a cheap one.
    assert (span["input_tokens"], span["output_tokens"], span["cached_tokens"]) == (100, None, 0)
    assert span["cost_usd"] is None
    assert span["output"] == {"role": "assistant", "content": [{"type": "text", "text": "Hel"}]}


async def test_oversized_sse_frame_detaches_the_observer_but_not_the_client(
    gateway_context: GatewayContextFactory,
    gateway_workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
    caplog: pytest.LogCaptureFixture,
) -> None:
    frames = chat_frames()
    chunk = json.loads(frames[1].decode().removeprefix("data: "))
    chunk["choices"][0]["delta"]["content"] = "x" * OVERSIZED_FRAME_BYTES
    oversized = f"data: {json.dumps(chunk)}\n\n".encode()
    script = [frames[0], oversized, frames[10], frames[11], frames[12]]
    fake_provider.reply_stream(script)
    context = await gateway_context()
    # The client asked for usage itself, so every byte is forwarded unchanged.
    request = chat_request(stream_options={"include_usage": True})

    result = await execute(request, context)
    assert result.stream is not None
    received = await read_to_end(result.stream)

    assert b"".join(received) == b"".join(script)
    assert fake_provider.open_streams == 0
    detached = logged(caplog, "gateway.stream_observer_detached")
    assert [entry["reason"] for entry in detached] == ["frame_too_large"]
    span = await read_gateway_span(session_factory, gateway_workspace.project_id)
    assert (span["status"], span["status_message"]) == ("ok", None)
    # The usage chunk came after the observer detached: unknown usage, so no cost (never 0).
    assert (span["input_tokens"], span["output_tokens"], span["cost_usd"]) == (None, None, None)
