"""Real lock setup and task cleanup for worker control-loop tests."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Job
from app.jobs import queue

SessionFactory = async_sessionmaker[AsyncSession]


async def enqueue(factory: SessionFactory, kind: str = "effect") -> int:
    async with factory() as db:
        await queue.enqueue(db, kind)
        await db.commit()
        return (await db.scalars(select(Job.id).where(Job.kind == kind))).one()


async def row(factory: SessionFactory, job_id: int) -> Job:
    async with factory() as db:
        job = await db.get(Job, job_id)
        assert job is not None
        return job


async def settle(task: asyncio.Task[object]) -> None:
    if not task.done():
        task.cancel()
    await asyncio.gather(task, return_exceptions=True)


@asynccontextmanager
async def relation_lock(factory: SessionFactory) -> AsyncIterator[int]:
    async with factory() as db:
        pid = await db.scalar(text("SELECT pg_backend_pid()"))
        assert isinstance(pid, int)
        await db.execute(text("LOCK TABLE jobs IN ACCESS EXCLUSIVE MODE"))
        try:
            yield pid
        finally:
            await db.rollback()


async def wait_for_blocker(factory: SessionFactory, pid: int) -> None:
    async with asyncio.timeout(2), factory() as db:
        while True:
            await db.execute(text("SELECT pg_stat_clear_snapshot()"))
            waiting = await db.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                    "WHERE :pid = ANY(pg_blocking_pids(pid)))"
                ),
                {"pid": pid},
            )
            if waiting:
                return
            await asyncio.sleep(0.002)
