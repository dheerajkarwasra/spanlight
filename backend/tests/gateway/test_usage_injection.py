"""The usage chunk on a streamed chat completion: asked for by the gateway, kept from the client.

OpenAI sends usage on a stream only when `stream_options.include_usage` is set. A client that
set no `stream_options` gets it added upstream, and the usage-only chunk that results is read
for the span but never forwarded. A client that set `stream_options` itself gets the stream
unchanged.
"""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.gateway.execute import execute
from tests.gateway.support import CHAT_COST, chat_frames, chat_request, read_to_end
from tests.gateway_fixtures import GatewayContextFactory, GatewayWorkspace
from tests.gateway_support import FakeProvider, read_gateway_span

USAGE_ONLY_FRAME = 11  # `"choices":[]` with `usage`, see `chat_frames`


async def test_injected_usage_chunk_is_read_but_not_forwarded(
    gateway_context: GatewayContextFactory,
    gateway_workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    frames = chat_frames()
    fake_provider.reply_stream(frames)
    context = await gateway_context()

    result = await execute(chat_request(), context)
    assert result.stream is not None
    received = b"".join(await read_to_end(result.stream))

    [sent] = fake_provider.requests
    assert (fake_provider.streams_opened, fake_provider.open_streams) == (1, 0)
    assert sent.json["stream_options"] == {"include_usage": True}
    assert b'"choices":[]' not in received
    assert received == b"".join(f for i, f in enumerate(frames) if i != USAGE_ONLY_FRAME)
    span = await read_gateway_span(session_factory, gateway_workspace.project_id)
    assert span["status"] == "ok"
    assert (span["input_tokens"], span["output_tokens"], span["cached_tokens"]) == (19, 9, 8)
    assert span["cost_usd"] == CHAT_COST
    # The span's input is what the client sent, not the body the gateway sent upstream.
    assert "stream_options" not in span["input"]


async def test_usage_chunk_the_client_asked_for_is_forwarded_unchanged(
    gateway_context: GatewayContextFactory,
    gateway_workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    frames = chat_frames()
    fake_provider.reply_stream(frames)
    context = await gateway_context()

    request = chat_request(stream_options={"include_usage": True})
    result = await execute(request, context)
    assert result.stream is not None
    received = b"".join(await read_to_end(result.stream))

    [sent] = fake_provider.requests
    assert (fake_provider.streams_opened, fake_provider.open_streams) == (1, 0)
    assert sent.json["stream_options"] == {"include_usage": True}
    assert received == b"".join(frames)
    span = await read_gateway_span(session_factory, gateway_workspace.project_id)
    assert (span["input_tokens"], span["output_tokens"], span["cached_tokens"]) == (19, 9, 8)
    assert span["cost_usd"] == CHAT_COST


async def test_a_client_that_turned_usage_off_gets_an_unpriced_span_not_a_zero_one(
    gateway_context: GatewayContextFactory,
    gateway_workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    frames = [f for i, f in enumerate(chat_frames()) if i != USAGE_ONLY_FRAME]
    fake_provider.reply_stream(frames)  # OpenAI sends no usage chunk when told not to
    context = await gateway_context()

    request = chat_request(stream_options={"include_usage": False})
    result = await execute(request, context)
    assert result.stream is not None
    received = b"".join(await read_to_end(result.stream))

    [sent] = fake_provider.requests
    assert (fake_provider.streams_opened, fake_provider.open_streams) == (1, 0)
    assert sent.json["stream_options"] == {"include_usage": False}  # the client's choice stands
    assert received == b"".join(frames)
    span = await read_gateway_span(session_factory, gateway_workspace.project_id)
    assert span["status"] == "ok"
    assert (span["input_tokens"], span["output_tokens"], span["cost_usd"]) == (None, None, None)
