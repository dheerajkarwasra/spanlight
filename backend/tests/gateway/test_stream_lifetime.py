"""Lifetime starts at acquisition; finalization completion survives cancelled waiters."""

import asyncio

import pytest

from app.gateway import stream_forward, upstream_io
from app.gateway.adapters import UpstreamResponse
from app.gateway.execute import execute
from tests.gateway.support import chat_frames, chat_request, read_to_end
from tests.gateway_fixtures import GatewayContextFactory
from tests.gateway_support import FakeProvider


@pytest.mark.parametrize("empty", [False, True])
async def test_delayed_internal_consumer_cannot_receive_first_chunk(
    gateway_context: GatewayContextFactory,
    fake_provider: FakeProvider,
    monkeypatch: pytest.MonkeyPatch,
    empty: bool,
) -> None:
    monkeypatch.setattr(upstream_io, "STREAM_TOTAL_TIMEOUT_S", 0.05)
    fake_provider.reply_stream([] if empty else chat_frames())
    result = await execute(chat_request(), await gateway_context())
    await asyncio.sleep(0.1)
    assert result.stream is not None
    assert await read_to_end(result.stream) == []
    assert fake_provider.open_streams == 0
    assert result.stream_error is not None


async def test_second_close_joins_retained_cleanup_after_first_waiter_cancelled(
    gateway_context: GatewayContextFactory,
    fake_provider: FakeProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_provider.reply_stream(chat_frames())
    result = await execute(chat_request(), await gateway_context())
    assert result.stream is not None
    entered, release = asyncio.Event(), asyncio.Event()

    original = upstream_io.close_quietly

    async def held(response: UpstreamResponse | None) -> None:
        entered.set()
        await release.wait()
        await original(response)

    monkeypatch.setattr(stream_forward, "close_quietly", held)
    first = asyncio.create_task(result.stream.aclose())
    await asyncio.wait_for(entered.wait(), 1)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    second = asyncio.create_task(result.stream.aclose())
    try:
        await asyncio.sleep(0.02)
        assert not second.done()
        assert fake_provider.open_streams == 1
    finally:
        release.set()
        await second
    assert fake_provider.open_streams == 0


async def test_snapshot_joins_finalization_after_waiter_is_dropped(
    gateway_context: GatewayContextFactory,
    fake_provider: FakeProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_provider.reply_stream(chat_frames())
    result = await execute(chat_request(), await gateway_context())
    assert result.stream is not None
    entered, release = asyncio.Event(), asyncio.Event()
    original = upstream_io.close_quietly

    async def held(response: UpstreamResponse | None) -> None:
        entered.set()
        await release.wait()
        await original(response)

    monkeypatch.setattr(stream_forward, "close_quietly", held)
    first = asyncio.create_task(result.stream.aclose())
    await entered.wait()
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    del result, first
    joined = asyncio.gather(*stream_forward.pending_finalizations())
    try:
        await asyncio.sleep(0.02)
        assert not joined.done()
        assert fake_provider.open_streams == 1
    finally:
        release.set()
        await joined
    assert fake_provider.open_streams == 0


async def test_repeated_close_propagates_the_same_finalization_failure(
    gateway_context: GatewayContextFactory,
    fake_provider: FakeProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_provider.reply_stream(chat_frames())
    result = await execute(chat_request(), await gateway_context())
    assert result.stream is not None
    original = upstream_io.close_quietly
    failure = RuntimeError("local finalizer failure")

    async def failed(response: UpstreamResponse | None) -> None:
        await original(response)
        raise failure

    monkeypatch.setattr(stream_forward, "close_quietly", failed)
    for _ in range(2):
        with pytest.raises(RuntimeError) as caught:
            await result.stream.aclose()
        assert caught.value is failure
    assert fake_provider.open_streams == 0
