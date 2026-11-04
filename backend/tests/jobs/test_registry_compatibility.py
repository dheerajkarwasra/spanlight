"""Older registries must leave incompatible queued and expired work untouched."""

import asyncio
from typing import Any

import pytest
from sqlalchemy import text

from app.config import Settings
from app.db.models import JobStatus
from app.jobs import queue
from app.jobs.context import TaskContext
from app.jobs.worker import Worker
from tests.jobs.registry_support import SessionFactory, make_due, seed, snapshot


async def test_old_worker_preserves_new_kind_until_capable_worker_arrives(
    session_factory: SessionFactory, settings: Settings
) -> None:
    effects: list[tuple[str, dict[str, Any], int, int]] = []

    async def handler(context: TaskContext, payload: dict[str, Any]) -> None:
        effects.append((context.job.kind, payload, context.job.attempts, context.job.fence))

    new_id = await seed(session_factory, "repair_aggregates_v2", {"contract": 2, "bucket": "hour"})
    before = await snapshot(session_factory, new_id)
    old = Worker(session_factory, settings, handlers={"known": handler})
    capable = Worker(session_factory, settings, handlers={"repair_aggregates_v2": handler})
    for _ in range(3):
        await old.run_once()
        assert await snapshot(session_factory, new_id) == before
    known_id = await seed(session_factory, "known", {"contract": 1})
    assert await old.run_once()
    assert (await snapshot(session_factory, known_id))["status"] is JobStatus.DONE
    assert await snapshot(session_factory, new_id) == before
    assert await capable.run_once()
    assert not await capable.run_once()
    assert effects == [
        ("known", {"contract": 1}, 1, 1),
        ("repair_aggregates_v2", {"contract": 2, "bucket": "hour"}, 1, 1),
    ]
    assert (await snapshot(session_factory, new_id))["status"] is JobStatus.DONE


@pytest.mark.parametrize("registered", [False, True])
@pytest.mark.parametrize("attempts", [0, 1, 3])
async def test_unknown_expired_and_exhausted_jobs_survive_worker_polls(
    session_factory: SessionFactory, settings: Settings, registered: bool, attempts: int
) -> None:
    async def handler(_: TaskContext, __: dict[str, Any]) -> None:
        pass

    job_id = await seed(session_factory, "unknown", {"contract": 2}, attempts=attempts)
    before = await snapshot(session_factory, job_id)
    worker = Worker(session_factory, settings, handlers={"known": handler} if registered else {})
    for _ in range(3):
        await worker.run_once()
        assert await snapshot(session_factory, job_id) == before
    if registered:
        supported = await seed(session_factory, "known", attempts=3)
        assert not await worker.run_once()
        assert (await snapshot(session_factory, supported))["status"] is JobStatus.FAILED


async def test_two_capable_workers_claim_each_job_once(
    session_factory: SessionFactory, settings: Settings
) -> None:
    effects: list[int] = []

    async def handler(context: TaskContext, _: dict[str, Any]) -> None:
        effects.append(context.job.id)
        await asyncio.sleep(0)

    ids = [await seed(session_factory, "known"), await seed(session_factory, "known")]
    first = Worker(session_factory, settings, handlers={"known": handler})
    second = Worker(session_factory, settings, handlers={"known": handler})
    assert await asyncio.gather(first.run_once(), second.run_once()) == [True, True]
    assert sorted(effects) == ids
    for job_id in ids:
        row = await snapshot(session_factory, job_id)
        assert (row["status"], row["attempts"], row["fence"]) == (JobStatus.DONE, 1, 1)


async def test_occupied_long_slot_combines_exclusion_and_registry(
    session_factory: SessionFactory, settings: Settings
) -> None:
    started, release = asyncio.Event(), asyncio.Event()
    effects: list[str] = []

    async def long_handler(_: TaskContext, __: dict[str, Any]) -> None:
        started.set()
        await release.wait()

    async def short_handler(_: TaskContext, __: dict[str, Any]) -> None:
        effects.append("short")

    await seed(session_factory, "create_export")
    worker = Worker(
        session_factory, settings, handlers={"create_export": long_handler, "known": short_handler}
    )
    try:
        assert await worker.run_once()
        await asyncio.wait_for(started.wait(), timeout=2)
        long_id = await seed(session_factory, "create_export")
        unknown_id = await seed(session_factory, "enterprise_future")
        long_before = await snapshot(session_factory, long_id)
        unknown_before = await snapshot(session_factory, unknown_id)
        await seed(session_factory, "known")
        assert await worker.run_once()
        assert not await worker.run_once()
        assert effects == ["short"]
        assert await snapshot(session_factory, long_id) == long_before
        assert await snapshot(session_factory, unknown_id) == unknown_before
    finally:
        release.set()
        await worker._stop_long_job()


async def test_supported_retry_and_stale_fences_keep_attempt_accounting(
    session_factory: SessionFactory, settings: Settings
) -> None:
    calls: list[int] = []

    async def handler(context: TaskContext, _: dict[str, Any]) -> None:
        calls.append(context.job.attempts)
        if context.job.attempts == 2:
            raise ValueError("retry")

    job_id = await seed(session_factory, "known", attempts=1)
    stale = queue.ClaimedJob(job_id, "known", {}, 1, 3, 1)
    worker = Worker(session_factory, settings, handlers={"known": handler})
    assert await worker.run_once()
    retried = await snapshot(session_factory, job_id)
    assert (retried["status"], retried["attempts"], retried["fence"]) == (JobStatus.QUEUED, 2, 2)
    assert retried["last_error"] == "ValueError: retry"
    async with session_factory() as db:
        assert await db.scalar(text("SELECT :due > now()"), {"due": retried["run_after"]})
        for operation in (queue.complete, queue.extend_lease):
            with pytest.raises(queue.LeaseLostError):
                await operation(db, stale)
        with pytest.raises(queue.LeaseLostError):
            await queue.fail(db, stale, "stale")
    assert not await worker.run_once()
    await make_due(session_factory, job_id)
    assert await worker.run_once()
    final = await snapshot(session_factory, job_id)
    assert (final["status"], final["attempts"], final["fence"]) == (JobStatus.DONE, 3, 3)
    assert calls == [2, 3]
