"""A shared loss flusher retains only the pool from which it was created."""

import asyncio

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.process_shutdown import LossFlushOwner
from app.telemetry_losses import flush
from app.telemetry_losses.counter import LossCounter

pytestmark = pytest.mark.serial


async def test_noncreator_has_no_flush_database_users() -> None:
    creator = async_sessionmaker()
    other = async_sessionmaker()
    flush.start(creator, LossCounter())
    flush.start(other)
    try:
        assert flush.pending_tasks(sessions=other) == ()
        assert len(flush.pending_tasks(sessions=creator)) == 1
        assert not await flush.stop(timeout_s=0.1)
        assert len(flush.pending_tasks(sessions=creator)) == 1
        assert await flush.stop(timeout_s=0.1)
        assert flush.pending_tasks(sessions=creator) == ()
    finally:
        await flush.stop(timeout_s=0.1)
        await flush.stop(timeout_s=0.1)


async def test_creator_retains_pool_until_other_owner_exits_despite_waiter_cancellation() -> None:
    creator = LossFlushOwner(async_sessionmaker())
    other = LossFlushOwner(async_sessionmaker())
    flush.start(creator.sessions, LossCounter())
    creator.acquired = True
    flush.start(other.sessions)
    other.acquired = True
    closing = asyncio.create_task(creator.stop())
    await asyncio.sleep(0.02)
    assert not closing.done()
    closing.cancel()
    closing.cancel()
    await asyncio.sleep(0.02)
    assert not closing.done()
    assert len(flush.pending_tasks(sessions=creator.sessions)) == 1
    await other.stop()
    with pytest.raises(asyncio.CancelledError):
        await closing
    await creator.stop()
    assert flush.pending_tasks() == ()


async def test_noncreator_closes_first_and_failed_acquisition_releases_nothing() -> None:
    creator = LossFlushOwner(async_sessionmaker())
    other = LossFlushOwner(async_sessionmaker())
    failed = LossFlushOwner(async_sessionmaker())
    flush.start(creator.sessions, LossCounter())
    creator.acquired = True
    flush.start(other.sessions)
    other.acquired = True
    await failed.stop()
    await other.stop()
    await other.stop()
    assert len(flush.pending_tasks(sessions=creator.sessions)) == 1
    await creator.stop()
    assert flush.pending_tasks() == ()
