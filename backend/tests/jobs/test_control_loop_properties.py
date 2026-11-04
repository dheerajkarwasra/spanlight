"""Bounded generated sequences use real application-role locks, cancellation and recovery."""

import asyncio
from typing import Any

import pytest
from hypothesis import given
from hypothesis import settings as hypothesis_settings
from hypothesis import strategies as st
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.config import Settings
from app.db.models import JobStatus
from app.db.session import create_engine, create_session_factory
from app.jobs.context import TaskContext
from app.jobs.worker import Worker
from tests.jobs.control_loop_support import enqueue, relation_lock, row, settle, wait_for_blocker


@given(actions=st.lists(st.sampled_from(["empty", "deadline", "cancel"]), min_size=1, max_size=4))
@hypothesis_settings(max_examples=8, deadline=None, derandomize=True)
def test_lock_cancel_and_recovery_sequences(app_database_url: str, actions: list[str]) -> None:
    async def exercise() -> None:
        engine = create_engine(app_database_url, pool_size=3, max_overflow=0)
        factory = create_session_factory(engine)
        settings = Settings(database_url=app_database_url, secret_key="test", _env_file=None)
        effects: list[str] = []

        async def handler(_: TaskContext, __: dict[str, Any]) -> None:
            effects.append("delivered")

        worker = Worker(factory, settings, handlers={"effect": handler})
        try:
            for action in actions:
                if action == "empty":
                    assert not await worker.run_once()
                    continue
                job_id = await enqueue(factory)
                before = len(effects)
                async with relation_lock(factory) as pid:
                    task = asyncio.create_task(worker.run_once())
                    try:
                        await wait_for_blocker(factory, pid)
                        if action == "cancel":
                            task.cancel()
                            with pytest.raises(asyncio.CancelledError):
                                await task
                        else:
                            done, _ = await asyncio.wait({task}, timeout=1.5)
                            assert done
                            with pytest.raises((TimeoutError, SQLAlchemyError)):
                                await task
                        assert len(effects) == before
                    finally:
                        await settle(task)
                queued = await row(factory, job_id)
                assert (queued.status, queued.attempts, queued.fence) == (JobStatus.QUEUED, 0, 0)
                assert await worker.run_once()
                assert len(effects) == before + 1
                assert (await row(factory, job_id)).status is JobStatus.DONE
                async with factory() as db:
                    await db.execute(text("DELETE FROM jobs"))
                    await db.commit()
            assert engine.sync_engine.pool.checkedout() == 0
        finally:
            await engine.dispose()

    asyncio.run(exercise())
