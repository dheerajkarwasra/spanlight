"""Embedded hosts join their own request producers; lifespans retain shared flush pools."""

import asyncio

import pytest

from app import main
from app.config import Settings
from app.telemetry_losses import flush

pytestmark = pytest.mark.serial


@pytest.mark.parametrize("creator_first", [True, False])
async def test_nested_apps_close_shared_pool_only_after_last_user(
    settings: Settings,
    creator_first: bool,
) -> None:
    creator = main.create_app(settings)
    other = main.create_gateway_app(settings)
    creator_started, other_started = asyncio.Event(), asyncio.Event()
    creator_stop, other_stop = asyncio.Event(), asyncio.Event()

    async def run(application, started, stop) -> None:
        async with application.router.lifespan_context(application):
            started.set()
            await stop.wait()

    first = asyncio.create_task(run(creator, creator_started, creator_stop))
    await creator_started.wait()
    second = asyncio.create_task(run(other, other_started, other_stop))
    await other_started.wait()
    try:
        if creator_first:
            creator_stop.set()
            await asyncio.sleep(0.03)
            assert not first.done()
            assert len(flush.pending_tasks()) == 1
            first.cancel()
            first.cancel()
            await asyncio.sleep(0.03)
            assert not first.done()
            other_stop.set()
            await second
            with pytest.raises(asyncio.CancelledError):
                await first
        else:
            other_stop.set()
            await second
            assert len(flush.pending_tasks()) == 1
            creator_stop.set()
            await first
        assert flush.pending_tasks() == ()
        assert creator.state.gateway_http.is_closed
        assert other.state.gateway_http.is_closed
    finally:
        creator_stop.set()
        other_stop.set()
        await asyncio.gather(first, second, return_exceptions=True)


async def test_failed_noncreator_start_does_not_release_creator(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    creator = main.create_app(settings)
    other = main.create_gateway_app(settings)
    async with creator.router.lifespan_context(creator):

        def fail_runtime(*args: object, **kwargs: object) -> None:
            raise RuntimeError("test acquisition failure")

        monkeypatch.setattr(main, "build_runtime", fail_runtime)
        with pytest.raises(RuntimeError, match="test acquisition failure"):
            async with other.router.lifespan_context(other):
                pytest.fail("startup must fail")
        assert len(flush.pending_tasks()) == 1
        assert not creator.state.gateway_http.is_closed
    assert flush.pending_tasks() == ()
