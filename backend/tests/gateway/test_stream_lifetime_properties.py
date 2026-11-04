"""Bounded consumer scheduling variations use the real gateway stream and observer."""

import asyncio
import dataclasses

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.gateway import upstream_io
from app.gateway.execute import execute
from app.gateway.recorder import DirectSpans
from tests.gateway.support import chat_frames, chat_request, read_to_end
from tests.gateway_fixtures import GatewayContextFactory
from tests.gateway_support import FakeProvider, Stall


@pytest.mark.parametrize("empty", [False, True])
async def test_deadline_begins_after_delayed_first_byte(
    gateway_context: GatewayContextFactory,
    fake_provider: FakeProvider,
    monkeypatch: pytest.MonkeyPatch,
    empty: bool,
) -> None:
    monkeypatch.setattr(upstream_io, "STREAM_TOTAL_TIMEOUT_S", 0.05)
    ready = asyncio.Event()
    fake_provider.reply_stream([Stall(ready), *([] if empty else chat_frames())])
    context = await gateway_context()
    before = asyncio.get_running_loop().time()
    call = asyncio.create_task(execute(chat_request(), context))
    await asyncio.sleep(0.1)
    assert not call.done()
    ready.set()
    result = await call
    assert result.stream_deadline is not None
    assert result.stream_deadline > before + 0.1
    assert result.stream is not None
    received = await read_to_end(result.stream)
    assert bool(received) is not empty
    assert result.stream_error is None
    assert fake_provider.open_streams == 0


@given(reads=st.integers(0, 5), expire=st.booleans(), cancel_close=st.booleans())
@settings(
    max_examples=16,
    deadline=None,
    derandomize=True,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
async def test_read_pause_cancel_boundaries_preserve_one_finalization(
    gateway_context: GatewayContextFactory,
    fake_provider: FakeProvider,
    monkeypatch: pytest.MonkeyPatch,
    reads: int,
    expire: bool,
    cancel_close: bool,
) -> None:
    # Each example gets its own request, stream, span sink, and provider script.
    monkeypatch.setattr(upstream_io, "STREAM_TOTAL_TIMEOUT_S", 0.04)
    sink = DirectSpans()
    context = dataclasses.replace(await gateway_context(), span_sink=sink)
    frames = chat_frames()
    fake_provider.reply_stream(frames)
    result = await execute(chat_request(stream_options={"include_usage": True}), context)
    assert result.stream is not None
    for index in range(reads):
        assert await anext(result.stream) == frames[index]
    if expire:
        await asyncio.sleep(0.06)
        assert await read_to_end(result.stream) == []
        assert result.stream_error is not None
    else:
        assert await read_to_end(result.stream) == frames[reads:]
        assert result.stream_error is None
    close = asyncio.create_task(result.stream.aclose())
    if cancel_close:
        close.cancel()
        with pytest.raises(asyncio.CancelledError):
            await close
    else:
        await close
    await result.stream.aclose()
    assert fake_provider.open_streams == 0
    assert await sink.write(context, require_span=True)
    assert not await sink.write(context, require_span=True)
