"""Real row locks must not stop unrelated queue claims."""

import asyncio
from datetime import timedelta
from itertools import permutations

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.observability import JOBS_FINISHED
from app.db.models import Job, JobStatus
from app.jobs import queue
from tests.gateway_support import logged, metric_delta

SessionFactory = async_sessionmaker[AsyncSession]


async def _seed(factory: SessionFactory, kind: str, *, attempts: int = 0) -> int:
    async with factory() as db:
        await queue.enqueue(db, kind, max_attempts=2)
        await db.commit()
        job = (await db.scalars(select(Job).where(Job.kind == kind))).one()
        if attempts:
            await db.execute(
                update(Job)
                .where(Job.id == job.id)
                .values(
                    status=JobStatus.RUNNING,
                    attempts=attempts,
                    fence=attempts,
                    lease_until=func.now() - timedelta(seconds=1),
                )
            )
            await db.commit()
        return job.id


async def _row(factory: SessionFactory, job_id: int) -> Job:
    async with factory() as db:
        job = await db.get(Job, job_id)
        assert job is not None
        return job


async def _claim(factory: SessionFactory) -> queue.ClaimedJob | None:
    async with factory() as db:
        return await queue.claim_next(db)


async def test_expired_final_attempt_is_counted_and_logged(
    session_factory: SessionFactory, caplog: pytest.LogCaptureFixture
) -> None:
    """A job whose lease ran out on its final attempt (its worker died) has no worker left to
    record it: the claim that fails it counts and names it, as the worker does for a failure."""
    job_id = await _seed(session_factory, "noop", attempts=2)
    finished = metric_delta(JOBS_FINISHED, kind="noop", outcome="lease_expired")

    await _claim(session_factory)

    assert (await _row(session_factory, job_id)).status is JobStatus.FAILED
    assert finished.value == 1
    [event] = logged(caplog, "job_lease_expired")
    assert (event["job_id"], event["kind"]) == (job_id, "noop")
    assert "payload" not in event


async def test_expired_final_attempt_is_not_counted_when_the_claim_rolls_back(
    session_factory: SessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The expiry commits with the claim; counted before a failed commit, the next poll would
    count and log the same job again."""
    job_id = await _seed(session_factory, "noop", attempts=2)
    finished = metric_delta(JOBS_FINISHED, kind="noop", outcome="lease_expired")

    async with session_factory() as db:

        async def lost_commit() -> None:
            raise ConnectionError("commit lost")

        monkeypatch.setattr(db, "commit", lost_commit)
        with pytest.raises(ConnectionError):
            await queue.claim_next(db)

    assert (await _row(session_factory, job_id)).status is JobStatus.RUNNING
    assert finished.value == 0


@pytest.mark.parametrize("ordering", list(permutations(("locked", "exhausted", "reclaimable"))))
async def test_locked_exhausted_allows_two_workers_to_progress(
    session_factory: SessionFactory,
    ordering: tuple[str, ...],
) -> None:
    ids = {}
    for kind in ordering:
        ids[kind] = await _seed(session_factory, kind, attempts=1 if kind == "reclaimable" else 2)
    due_id = await _seed(session_factory, "due")
    async with session_factory() as locker:
        await locker.execute(select(Job).where(Job.id == ids["locked"]).with_for_update())
        # Completion before releasing the lock is the regression assertion. Cancellation
        # rolls back the blocked transaction on the old implementation.
        first, second = await asyncio.wait_for(
            asyncio.gather(_claim(session_factory), _claim(session_factory)),
            timeout=2,
        )
        assert first is not None and second is not None
        assert {first.id, second.id} == {ids["reclaimable"], due_id}
        reclaimed = first if first.id == ids["reclaimable"] else second
        assert (reclaimed.attempts, reclaimed.fence) == (2, 2)
        assert (await _row(session_factory, ids["locked"])).status is JobStatus.RUNNING
        assert (await _row(session_factory, ids["exhausted"])).status is JobStatus.FAILED
        await locker.rollback()
    assert await _claim(session_factory) is None
    exhausted = await _row(session_factory, ids["locked"])
    assert (exhausted.status, exhausted.attempts, exhausted.fence, exhausted.lease_until) == (
        JobStatus.FAILED,
        2,
        2,
        None,
    )
    assert exhausted.last_error == "lease expired after the final attempt"
    old = queue.ClaimedJob(ids["reclaimable"], "reclaimable", {}, 1, 2, 1)
    async with session_factory() as db:
        with pytest.raises(queue.LeaseLostError):
            await queue.complete(db, old)
        with pytest.raises(queue.LeaseLostError):
            await queue.extend_lease(db, old)
        await queue.complete(db, reclaimed)


async def test_extended_final_lease_is_not_expired(session_factory: SessionFactory) -> None:
    job_id = await _seed(session_factory, "extended", attempts=2)
    held = queue.ClaimedJob(job_id, "extended", {}, 2, 2, 2)
    async with session_factory() as db:
        await queue.extend_lease(db, held)
    assert await _claim(session_factory) is None
    row = await _row(session_factory, job_id)
    assert (row.status, row.attempts, row.fence) == (JobStatus.RUNNING, 2, 2)
    assert row.lease_until is not None
    async with session_factory() as db:
        await queue.complete(db, held)


async def test_exhaustion_and_claim_roll_back_together(session_factory: SessionFactory) -> None:
    exhausted_id = await _seed(session_factory, "exhausted", attempts=2)
    due_id = await _seed(session_factory, "due")
    async with session_factory() as owner:
        connection = await owner.connection()
        async with AsyncSession(bind=connection, join_transaction_mode="create_savepoint") as db:
            claimed = await queue.claim_next(db)
            assert claimed is not None and claimed.id == due_id
            exhausted = await db.get(Job, exhausted_id)
            assert exhausted is not None and exhausted.status is JobStatus.FAILED
        await owner.rollback()
    assert (await _row(session_factory, exhausted_id)).status is JobStatus.RUNNING
    due = await _row(session_factory, due_id)
    assert (due.status, due.attempts, due.fence) == (JobStatus.QUEUED, 0, 0)
    claimed = await _claim(session_factory)
    assert claimed is not None and claimed.id == due_id
    assert (claimed.attempts, claimed.fence) == (1, 1)
    assert await _claim(session_factory) is None


async def test_locked_reclaimable_job_waits_for_its_owner(session_factory: SessionFactory) -> None:
    reclaimable_id = await _seed(session_factory, "reclaimable", attempts=1)
    due_id = await _seed(session_factory, "due")
    async with session_factory() as locker:
        await locker.execute(select(Job).where(Job.id == reclaimable_id).with_for_update())
        claimed = await asyncio.wait_for(_claim(session_factory), timeout=2)
        assert claimed is not None and claimed.id == due_id
        assert await asyncio.wait_for(_claim(session_factory), timeout=2) is None
        await locker.rollback()
    reclaimed = await _claim(session_factory)
    assert reclaimed is not None and reclaimed.id == reclaimable_id
    assert (reclaimed.attempts, reclaimed.fence) == (2, 2)


async def test_uncommitted_lease_extension_survives_sweep(session_factory: SessionFactory) -> None:
    extended_id = await _seed(session_factory, "extended", attempts=2)
    due_id = await _seed(session_factory, "due")
    async with session_factory() as extender:
        await extender.execute(
            update(Job)
            .where(Job.id == extended_id)
            .values(lease_until=func.now() + timedelta(minutes=5))
        )
        claimed = await asyncio.wait_for(_claim(session_factory), timeout=2)
        assert claimed is not None and claimed.id == due_id
        await extender.commit()
    assert await _claim(session_factory) is None
    row = await _row(session_factory, extended_id)
    assert (row.status, row.attempts, row.fence) == (JobStatus.RUNNING, 2, 2)
    assert row.lease_until is not None
