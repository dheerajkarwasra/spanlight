"""Database setup and snapshots for real worker registry compatibility tests."""

from datetime import timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Job, JobStatus
from app.jobs import queue

SessionFactory = async_sessionmaker[AsyncSession]


async def seed(
    factory: SessionFactory,
    kind: str,
    payload: dict[str, Any] | None = None,
    *,
    attempts: int = 0,
    max_attempts: int = 3,
) -> int:
    async with factory() as db:
        await queue.enqueue(db, kind, payload, max_attempts=max_attempts)
        job_id = (await db.scalars(select(Job.id).order_by(Job.id.desc()).limit(1))).one()
        if attempts:
            await db.execute(
                update(Job)
                .where(Job.id == job_id)
                .values(
                    status=JobStatus.RUNNING,
                    attempts=attempts,
                    fence=attempts,
                    lease_until=func.now() - timedelta(seconds=1),
                )
            )
        await db.commit()
        return job_id


async def snapshot(factory: SessionFactory, job_id: int) -> dict[str, Any]:
    async with factory() as db:
        return dict(
            (await db.execute(select(Job.__table__).where(Job.id == job_id))).mappings().one()
        )


async def make_due(factory: SessionFactory, job_id: int) -> None:
    async with factory() as db:
        await db.execute(
            update(Job).where(Job.id == job_id).values(run_after=func.now() - timedelta(seconds=1))
        )
        await db.commit()
