"""Many interrupted streams in a row leave nothing behind.

Every provider response is closed, every span write has finished and given its slot back, and
each call is exactly one span. A leak here would grow with traffic: open connections in the
provider pool, writes that never finish, or slots that run out until no span is written.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.gateway import recorder
from tests.gateway.interruptions import (
    close_unread,
    disconnect_mid_stream,
    upstream_breaks,
    upstream_goes_quiet,
)
from tests.gateway_fixtures import GatewayContextFactory, GatewayWorkspace
from tests.gateway_support import FakeProvider, drain_recorder, read_gateway_spans

ROUNDS = 50


async def test_no_provider_response_is_left_open_after_fifty_interruptions(
    gateway_context: GatewayContextFactory,
    gateway_workspace: GatewayWorkspace,
    gateway_settings: Settings,
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
    short_idle_timeout: float,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(recorder, "_writers", None)  # this test's writers, counted from empty
    keyed = await gateway_context(keyed=True)
    stored_route = await gateway_context()

    for _ in range(ROUNDS):
        await disconnect_mid_stream(fake_provider, keyed)
        await close_unread(fake_provider, keyed)
        await upstream_breaks(fake_provider, stored_route)
        await upstream_goes_quiet(fake_provider, stored_route)
    await drain_recorder()

    calls = 4 * ROUNDS
    assert (fake_provider.streams_opened, fake_provider.open_streams) == (calls, 0)
    assert fake_provider.pending == 0
    writers = recorder._writers
    assert writers is not None
    assert not writers.pending
    assert writers.slots._value == gateway_settings.gateway_record_concurrency  # every slot back
    spans = await read_gateway_spans(session_factory, gateway_workspace.project_id)
    assert len(spans) == calls
    assert len({span["span_id"] for span in spans}) == calls
    assert {span["status"] for span in spans} == {"error"}
