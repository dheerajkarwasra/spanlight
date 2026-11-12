"""Real COMMIT and checkout cancellation must settle before a claim can execute."""

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import Settings
from app.db.models import Job, JobStatus
from app.db.session import create_engine, create_session_factory
from app.jobs.context import TaskContext
from app.jobs.worker import Worker
from tests.jobs.control_loop_support import (
    SessionFactory,
    enqueue,
    relation_lock,
    row,
    settle,
    wait_for_blocker,
)
from tests.jobs.rollback_proxy_support import rollback_proxy

# The worker's own limits bound every case (0.5 s to claim, 250 ms per statement): beside the
# parallel suite's load a claim through the proxy has timed out on a healthy database.
pytestmark = pytest.mark.serial


@asynccontextmanager
async def commit_lock(factory: SessionFactory) -> AsyncIterator[int]:
    """A deferred trigger removes SQL limits to exercise the outer COMMIT deadline."""
    async with factory() as db:
        await db.execute(
            text("""
            CREATE FUNCTION control_loop_commit_wait() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                PERFORM set_config('lock_timeout', '0', true);
                PERFORM set_config('statement_timeout', '0', true);
                PERFORM pg_advisory_xact_lock(7162002);
                RETURN NEW;
            END $$
        """)
        )
        await db.execute(
            text("""
            CREATE CONSTRAINT TRIGGER control_loop_commit_wait AFTER UPDATE ON jobs
            DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN (NEW.status = 'running')
            EXECUTE FUNCTION control_loop_commit_wait()
        """)
        )
        await db.commit()
    try:
        async with factory() as holder:
            pid = await holder.scalar(text("SELECT pg_backend_pid()"))
            assert isinstance(pid, int)
            await holder.execute(text("SELECT pg_advisory_xact_lock(7162002)"))
            try:
                yield pid
            finally:
                await holder.rollback()
    finally:
        async with factory() as db:
            await db.execute(text("DROP TRIGGER control_loop_commit_wait ON jobs"))
            await db.execute(text("DROP FUNCTION control_loop_commit_wait()"))
            await db.commit()


async def assert_reusable(factory: SessionFactory, engine: AsyncEngine) -> None:
    async with factory() as db:
        limits = (
            await db.execute(
                text("SELECT current_setting('lock_timeout'), current_setting('statement_timeout')")
            )
        ).one()
        assert limits == ("0", "0")
        assert (
            await db.scalar(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname=current_database() AND pid<>pg_backend_pid() "
                    "AND state LIKE 'idle in transaction%'"
                )
            )
            == 0
        )
        # Handler SQL must retain its normal duration, beyond the short operation SQL limit.
        await db.execute(text("SELECT pg_sleep(0.3)"))
    assert engine.sync_engine.pool.checkedout() == 0


@pytest.mark.parametrize("cancel", [False, True])
async def test_uncertain_claim_commit_never_executes_and_cleanup_reuses_pool(
    session_factory: SessionFactory,
    settings: Settings,
    engine: AsyncEngine,
    cancel: bool,
) -> None:
    effect = asyncio.Event()

    async def handler(_: TaskContext, __: dict[str, Any]) -> None:
        effect.set()

    job_id = await enqueue(session_factory)
    worker = Worker(session_factory, settings, handlers={"effect": handler})
    async with commit_lock(session_factory) as pid:
        started = time.monotonic()
        task = asyncio.create_task(worker.run_once())
        try:
            await wait_for_blocker(session_factory, pid)
            if cancel:
                started = time.monotonic()
                task.cancel()
            done, _ = await asyncio.wait({task}, timeout=1.5)
            assert done, "COMMIT cancellation failed to settle"
            with pytest.raises(asyncio.CancelledError if cancel else TimeoutError):
                await task
            print("COMMIT_CLEANUP", {"cancel": cancel, "elapsed": time.monotonic() - started})
            assert not effect.is_set()
            uncertain = await row(session_factory, job_id)
            assert (uncertain.status, uncertain.attempts, uncertain.fence) == (
                JobStatus.QUEUED,
                0,
                0,
            )
        finally:
            await settle(task)
    await assert_reusable(session_factory, engine)
    assert await worker.run_once()
    assert effect.is_set()
    assert (await row(session_factory, job_id)).status is JobStatus.DONE


async def test_claim_checkout_deadline_does_not_cancel_the_pool_owner(settings: Settings) -> None:
    engine = create_engine(settings.database_url, pool_size=1, max_overflow=0)
    factory = create_session_factory(engine)
    try:
        async with factory() as holder:
            pid = await holder.scalar(text("SELECT pg_backend_pid()"))
            worker = Worker(factory, settings, handlers={})
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(worker.run_once(), timeout=1.5)
            assert await holder.scalar(text("SELECT pg_backend_pid()")) == pid
        assert not await worker.run_once()
        await assert_reusable(factory, engine)
    finally:
        await engine.dispose()


async def test_shutdown_cancellation_survives_uncertain_failure_persistence(
    session_factory: SessionFactory,
    settings: Settings,
    engine: AsyncEngine,
) -> None:
    entered = asyncio.Event()

    async def handler(_: TaskContext, __: dict[str, Any]) -> None:
        entered.set()
        await asyncio.Event().wait()

    job_id = await enqueue(session_factory)
    worker = Worker(session_factory, settings, handlers={"effect": handler})
    task = asyncio.create_task(worker.run_once())
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        async with session_factory() as holder:
            await holder.execute(select(Job).where(Job.id == job_id).with_for_update())
            started = time.monotonic()
            task.cancel()
            done, _ = await asyncio.wait({task}, timeout=1.5)
            assert done, "shutdown failure persistence remained blocked"
            with pytest.raises(asyncio.CancelledError):
                await task
            print("SHUTDOWN_CLEANUP", time.monotonic() - started)
            assert (await row(session_factory, job_id)).status is JobStatus.RUNNING
            await holder.rollback()
    finally:
        await settle(task)
    await assert_reusable(session_factory, engine)


async def test_failed_operation_discards_connection_without_waiting_for_rollback(
    session_factory: SessionFactory,
    settings: Settings,
) -> None:
    effect = asyncio.Event()

    async def handler(_: TaskContext, __: dict[str, Any]) -> None:
        effect.set()

    async with rollback_proxy(settings.database_url) as proxy:
        engine = create_engine(proxy.client_url, pool_size=1, max_overflow=0)
        factory = create_session_factory(engine)
        worker = Worker(factory, settings, handlers={"effect": handler})
        task = None
        try:
            async with factory() as db:
                failed_pid = await db.scalar(text("SELECT pg_backend_pid()"))
            proxy.hold_rollback = True
            async with relation_lock(session_factory) as pid:
                task = asyncio.create_task(worker.run_once())
                await wait_for_blocker(session_factory, pid)
                started = time.monotonic()
                done, _ = await asyncio.wait({task}, timeout=1.5)
                assert done, (
                    "session exit waited for a withheld ROLLBACK past the operation deadline"
                )
                with pytest.raises((TimeoutError, SQLAlchemyError)):
                    await task
                proxy.hold_rollback = False
                assert engine.sync_engine.pool.checkedout() == 0
                print("WITHHELD_ROLLBACK_CLEANUP", time.monotonic() - started)
            async with factory() as db:
                recovered_pid = await db.scalar(text("SELECT pg_backend_pid()"))
                assert recovered_pid != failed_pid
            job_id = await enqueue(factory)
            assert await worker.run_once()
            assert effect.is_set()
            assert (await row(factory, job_id)).status is JobStatus.DONE
            async with factory() as db:
                assert await db.scalar(text("SELECT pg_backend_pid()")) == recovered_pid
            await assert_reusable(factory, engine)
        finally:
            proxy.release.set()
            if task is not None:
                await settle(task)
            await engine.dispose()
