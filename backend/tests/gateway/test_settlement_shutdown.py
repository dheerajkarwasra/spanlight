"""Settlement cancellation must complete before returning database ownership."""

import asyncio

import pytest

from app.gateway import settle_helpers


async def test_settlement_timeout_cancels_and_joins_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settle_helpers, "DRAIN_TIMEOUT_SECONDS", 0.01)
    entered = asyncio.Event()
    cleaned = asyncio.Event()

    async def settlement() -> None:
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0.01)
            cleaned.set()

    waiter = asyncio.create_task(settle_helpers.shielded(settlement()))
    await entered.wait()
    waiter.cancel()
    await asyncio.gather(waiter, return_exceptions=True)
    tasks = tuple(settle_helpers._settling)
    try:
        await settle_helpers.drain_settling()
        assert cleaned.is_set()
        assert all(task.done() for task in tasks)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def test_noncooperative_settlement_stays_owned_after_join_deadline() -> None:
    entered = asyncio.Event()
    cancelled = asyncio.Event()
    release = asyncio.Event()

    async def settlement() -> None:
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancelled.set()
            await release.wait()

    waiter = asyncio.create_task(settle_helpers.shielded(settlement()))
    await entered.wait()
    loop = asyncio.get_running_loop()
    try:
        assert not await settle_helpers.join_settling(
            cancel_at=loop.time(), deadline=loop.time() + 0.02
        )
        assert cancelled.is_set()
        assert len(settle_helpers.pending_settlements()) == 1
        assert not waiter.done()
        assert not await settle_helpers.join_settling(
            cancel_at=loop.time(), deadline=loop.time() + 0.02
        )
        assert not waiter.done()
    finally:
        release.set()
        await waiter
    assert settle_helpers.pending_settlements() == ()


async def test_repeated_drain_waiter_cancellation_never_skips_settlement_join(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settle_helpers, "DRAIN_TIMEOUT_SECONDS", 0)
    entered = asyncio.Event()
    cleaning = asyncio.Event()
    release = asyncio.Event()

    async def settlement() -> None:
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release.wait()

    caller = asyncio.create_task(settle_helpers.shielded(settlement()))
    await entered.wait()
    drain = asyncio.create_task(settle_helpers.drain_settling())
    await cleaning.wait()
    for _ in range(3):
        drain.cancel()
        await asyncio.sleep(0)
        assert not drain.done()
        assert len(settle_helpers.pending_settlements()) == 1
    release.set()
    await asyncio.gather(caller, drain, return_exceptions=True)
    assert drain.cancelled()
    assert settle_helpers.pending_settlements() == ()
