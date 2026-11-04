"""Domain fence checks use fresh wall time after waiting and retain the lock through commit."""

import asyncio

import pytest
from sqlalchemy import text

from app.jobs.fencing import lock_live_job
from app.jobs.queue import LeaseLostError
from tests.attribution.daily_support import queued
from tests.jobs.bypass_support import SessionFactory, execute


async def test_lease_expiring_during_identity_lock_wait_is_rejected(
    session_factory: SessionFactory,
) -> None:
    job = await queued(
        session_factory, "schedule_attribution_daily", {"scheduled_period": "2026-10-30"}
    )
    await execute(
        session_factory,
        "UPDATE jobs SET lease_until=clock_timestamp()+interval '100 milliseconds' WHERE id=:id",
        {"id": job.id},
    )
    async with session_factory() as holder, session_factory() as waiter:
        await holder.execute(text("SELECT id FROM jobs WHERE id=:id FOR UPDATE"), {"id": job.id})
        task = asyncio.create_task(lock_live_job(waiter, job))
        await asyncio.sleep(0.15)
        await holder.commit()
        with pytest.raises(LeaseLostError, match="live commit lease"):
            await task
        await waiter.rollback()


async def test_fence_lock_blocks_replacement_until_domain_commit(
    session_factory: SessionFactory,
) -> None:
    job = await queued(
        session_factory, "schedule_attribution_daily", {"scheduled_period": "2026-10-30"}
    )
    async with session_factory() as domain, session_factory() as reclaimer:
        await lock_live_job(domain, job)
        waiting = asyncio.create_task(
            reclaimer.execute(text("UPDATE jobs SET fence=fence+1 WHERE id=:id"), {"id": job.id})
        )
        await asyncio.sleep(0.05)
        assert not waiting.done()
        await domain.commit()
        await waiting
        await reclaimer.commit()
    async with session_factory() as stale:
        with pytest.raises(LeaseLostError, match="live commit lease"):
            await lock_live_job(stale, job)


async def test_transaction_start_time_cannot_keep_an_expired_job_live(
    session_factory: SessionFactory,
) -> None:
    job = await queued(
        session_factory, "schedule_attribution_daily", {"scheduled_period": "2026-10-30"}
    )
    async with session_factory() as db:
        await db.execute(text("SELECT now()"))
        await execute(
            session_factory,
            "UPDATE jobs SET lease_until=clock_timestamp()-interval '1 millisecond' WHERE id=:id",
            {"id": job.id},
        )
        with pytest.raises(LeaseLostError, match="live commit lease"):
            await lock_live_job(db, job)
