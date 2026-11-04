"""The stack cannot dispose a pool while a retained stream finalizer can still enqueue."""

import asyncio
import contextlib

import pytest

from app import main
from app.config import Settings
from app.gateway import stream_forward, upstream_io
from app.gateway.adapters import UpstreamResponse
from app.gateway.execute import execute
from app.process_shutdown import ProcessShutdown, ShutdownBudget, current
from app.telemetry_losses import flush
from tests.gateway.support import chat_frames, chat_request
from tests.gateway_fixtures import GatewayContextFactory
from tests.gateway_support import FakeProvider

pytestmark = pytest.mark.serial


async def test_lifespan_waits_for_finalizer_even_after_join_deadline(
    settings: Settings,
    gateway_context: GatewayContextFactory,
    fake_provider: FakeProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_provider.reply_stream(chat_frames())
    result = await execute(chat_request(), await gateway_context())
    assert result.stream is not None
    held, release = asyncio.Event(), asyncio.Event()
    original = upstream_io.close_quietly

    async def close(response: UpstreamResponse | None) -> None:
        held.set()
        await release.wait()
        await original(response)

    monkeypatch.setattr(stream_forward, "close_quietly", close)
    first = asyncio.create_task(result.stream.aclose())
    await held.wait()
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    # The gateway fixture has its own lifespan; leave one flusher owner in this test.
    await flush.stop(timeout_s=1)
    application = main.create_gateway_app(settings)
    process = ProcessShutdown(ShutdownBudget(0.1, 0.01, 0.02, 0.02))

    async def lifespan() -> None:
        token = current.set(process)
        try:
            async with application.router.lifespan_context(application):
                pass
        finally:
            current.reset(token)

    owner = asyncio.create_task(lifespan())
    try:
        await asyncio.sleep(0.15)
        assert not owner.done()
        assert not application.state.gateway_http.is_closed
        owner.cancel()
        await asyncio.sleep(0)
        assert not owner.done()
    finally:
        release.set()
        await result.stream.aclose()
        with contextlib.suppress(asyncio.CancelledError):
            await owner
    assert application.state.gateway_http.is_closed
    assert not process.accounting_complete
