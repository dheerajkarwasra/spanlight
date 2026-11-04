"""A due backup or erasure reaches its handler beside a running experiment; the queue gauges say
what waits. Readiness Task 7D."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from prometheus_client import REGISTRY
from sqlalchemy import func, select, update

from app.config import Settings
from app.db.models import Job, JobStatus
from app.jobs import queue
from app.jobs import worker as worker_module
from app.jobs.context import TaskContext
from app.jobs.queue_gauges import sample_queue_gauges
from app.jobs.worker import Worker
from tests.jobs.control_loop_support import SessionFactory, relation_lock, row, settle


def _ago(seconds: float) -> datetime:
    return datetime.now(UTC) - timedelta(seconds=seconds)


async def _enqueue(factory: SessionFactory, kind: str, run_after: datetime | None = None) -> int:
    async with factory() as db:
        await queue.enqueue(db, kind, run_after=run_after)
        await db.commit()
        return (await db.scalars(select(Job.id).where(Job.kind == kind))).one()


def _blocking_handler(entered: asyncio.Event, release: asyncio.Event) -> Any:
    async def handler(context: TaskContext, _: dict[str, Any]) -> None:
        await context.heartbeat()
        entered.set()
        await release.wait()

    return handler


async def _wait_done(factory: SessionFactory, job_id: int) -> None:
    async with asyncio.timeout(2):
        while True:
            if (await row(factory, job_id)).status is JobStatus.DONE:
                return
            await asyncio.sleep(0.002)


def _sample(name: str, kind: str) -> float | None:
    return REGISTRY.get_sample_value(name, {"kind": kind})


async def test_due_maintenance_enters_beside_a_running_experiment(
    session_factory: SessionFactory, settings: Settings
) -> None:
    experiment_entered, release_experiment = asyncio.Event(), asyncio.Event()
    backup_entered, release_backup = asyncio.Event(), asyncio.Event()
    erasure_entered, release_erasure = asyncio.Event(), asyncio.Event()
    worker = Worker(
        session_factory,
        settings,
        handlers={
            "run_experiment": _blocking_handler(experiment_entered, release_experiment),
            "backup_database": _blocking_handler(backup_entered, release_backup),
            "erase_end_user": _blocking_handler(erasure_entered, release_erasure),
            "create_export": _blocking_handler(asyncio.Event(), asyncio.Event()),
        },
    )
    try:
        experiment_id = await _enqueue(session_factory, "run_experiment")
        assert await worker.run_once()
        await asyncio.wait_for(experiment_entered.wait(), timeout=2)

        # Due 400 s ago: already past the 300 s target. Backup and erasure arrive together.
        backup_id = await _enqueue(session_factory, "backup_database", _ago(400))
        erasure_id = await _enqueue(session_factory, "erase_end_user", _ago(400))
        export_id = await _enqueue(session_factory, "create_export", _ago(400))
        assert await worker.run_once(), "due backup stayed queued behind the experiment"
        await asyncio.wait_for(backup_entered.wait(), timeout=2)
        assert (await row(session_factory, experiment_id)).status is JobStatus.RUNNING

        # One maintenance slot: the erasure waits for the backup, the export for the experiment.
        assert not await worker.run_once()
        for job_id in (erasure_id, export_id):
            waiting = await row(session_factory, job_id)
            assert (waiting.status, waiting.attempts, waiting.fence) == (JobStatus.QUEUED, 0, 0)

        release_backup.set()
        await _wait_done(session_factory, backup_id)
        assert await worker.run_once()
        await asyncio.wait_for(erasure_entered.wait(), timeout=2)
        assert (await row(session_factory, export_id)).status is JobStatus.QUEUED
    finally:
        release_experiment.set()
        release_backup.set()
        release_erasure.set()
        await worker._stop_long_job()


async def test_experiments_and_exports_never_take_the_maintenance_slot(
    session_factory: SessionFactory, settings: Settings
) -> None:
    entered, release = asyncio.Event(), asyncio.Event()
    worker = Worker(
        session_factory,
        settings,
        handlers={
            "run_experiment": _blocking_handler(entered, release),
            "create_export": _blocking_handler(asyncio.Event(), asyncio.Event()),
        },
    )
    try:
        await _enqueue(session_factory, "run_experiment")
        assert await worker.run_once()
        await asyncio.wait_for(entered.wait(), timeout=2)
        async with session_factory() as db:
            await queue.enqueue(db, "run_experiment", {"second": True}, run_after=_ago(400))
            await queue.enqueue(db, "create_export", run_after=_ago(400))
            await db.commit()
        assert not await worker.run_once()
        assert not await worker.run_once()
        async with session_factory() as db:
            waiting = (
                await db.execute(
                    select(Job.status, Job.attempts, Job.fence).where(Job.status != "running")
                )
            ).all()
        assert waiting == [(JobStatus.QUEUED, 0, 0)] * 2
    finally:
        release.set()
        await worker._stop_long_job()


async def test_future_maintenance_is_not_claimed_early(
    session_factory: SessionFactory, settings: Settings
) -> None:
    entered, release = asyncio.Event(), asyncio.Event()
    worker = Worker(
        session_factory,
        settings,
        handlers={
            "run_experiment": _blocking_handler(entered, release),
            "backup_database": _blocking_handler(asyncio.Event(), asyncio.Event()),
        },
    )
    try:
        await _enqueue(session_factory, "run_experiment")
        assert await worker.run_once()
        await asyncio.wait_for(entered.wait(), timeout=2)
        backup_id = await _enqueue(session_factory, "backup_database", _ago(-3600))
        assert not await worker.run_once()
        future = await row(session_factory, backup_id)
        assert (future.status, future.attempts, future.fence) == (JobStatus.QUEUED, 0, 0)
        await sample_queue_gauges(session_factory, worker.handlers.keys())
        assert _sample("spanlight_jobs_queued_due", "backup_database") == 0
    finally:
        release.set()
        await worker._stop_long_job()


async def test_short_jobs_run_while_both_long_slots_are_busy(
    session_factory: SessionFactory, settings: Settings
) -> None:
    experiment_entered, backup_entered = asyncio.Event(), asyncio.Event()
    release = asyncio.Event()
    effect = asyncio.Event()

    async def short(_: TaskContext, __: dict[str, Any]) -> None:
        effect.set()

    worker = Worker(
        session_factory,
        settings,
        handlers={
            "run_experiment": _blocking_handler(experiment_entered, release),
            "backup_database": _blocking_handler(backup_entered, release),
            "effect": short,
        },
    )
    try:
        await _enqueue(session_factory, "run_experiment")
        await _enqueue(session_factory, "backup_database")
        assert await worker.run_once()
        assert await worker.run_once()
        await asyncio.wait_for(experiment_entered.wait(), timeout=2)
        await asyncio.wait_for(backup_entered.wait(), timeout=2)
        effect_id = await _enqueue(session_factory, "effect")
        assert await worker.run_once()
        assert effect.is_set()
        assert (await row(session_factory, effect_id)).status is JobStatus.DONE
    finally:
        release.set()
        await worker._stop_long_job()


async def _no_schedule(*_: Any, **__: Any) -> list[str]:
    return []


async def test_shutdown_requeues_both_long_slots_and_samples_the_queue(
    session_factory: SessionFactory, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The scheduler's own nightly backup row (due at 03:00) would take the slot first.
    monkeypatch.setattr(worker_module, "schedule_periodic", _no_schedule)
    experiment_entered, backup_entered = asyncio.Event(), asyncio.Event()
    never = asyncio.Event()
    worker = Worker(
        session_factory,
        settings,
        handlers={
            "run_experiment": _blocking_handler(experiment_entered, never),
            "backup_database": _blocking_handler(backup_entered, never),
        },
    )
    experiment_id = await _enqueue(session_factory, "run_experiment")
    backup_id = await _enqueue(session_factory, "backup_database")
    samples_before = (
        REGISTRY.get_sample_value("spanlight_jobs_queue_samples_total", {"outcome": "ok"}) or 0
    )
    stop = asyncio.Event()
    task = asyncio.create_task(worker.run_forever(stop))
    try:
        await asyncio.wait_for(experiment_entered.wait(), timeout=5)
        await asyncio.wait_for(backup_entered.wait(), timeout=5)
        # The heartbeat loop's first tick samples the queue with the registry frozen at start.
        async with asyncio.timeout(5):
            while True:
                samples = REGISTRY.get_sample_value(
                    "spanlight_jobs_queue_samples_total", {"outcome": "ok"}
                )
                if (samples or 0) > samples_before:
                    break
                await asyncio.sleep(0.01)
        assert _sample("spanlight_jobs_queued_due", "other") is not None
        stop.set()
        await asyncio.wait_for(task, timeout=5)
    finally:
        await settle(task)
    async with session_factory() as db:
        jobs = (
            await db.scalars(
                select(Job).where(Job.id.in_([experiment_id, backup_id])).order_by(Job.id)
            )
        ).all()
    assert [(job.status, job.attempts, job.last_error) for job in jobs] == [
        (JobStatus.QUEUED, 1, "RuntimeError: worker shut down")
    ] * 2


async def test_queue_gauges_report_due_age_depth_and_reclaimable_leases(
    session_factory: SessionFactory,
) -> None:
    kinds = {"backup_database", "noop"}
    await _enqueue(session_factory, "backup_database", _ago(120))
    await _enqueue(session_factory, "enterprise_future", _ago(600))
    async with session_factory() as db:
        await queue.enqueue(db, "backup_database", {"n": 2}, run_after=_ago(30))
        await queue.enqueue(db, "backup_database", {"n": 3}, run_after=_ago(-3600))
        await queue.enqueue(db, "noop", {"n": 1})
        await queue.enqueue(db, "noop", {"n": 2}, max_attempts=1)
        await db.commit()
        reclaimable = await queue.claim_next(db, supported_kinds=["noop"])
        exhausted = await queue.claim_next(db, supported_kinds=["noop"])
        assert reclaimable is not None and exhausted is not None
        await db.execute(
            update(Job)
            .where(Job.id == reclaimable.id)
            .values(lease_until=func.now() - timedelta(seconds=10))
        )
        await db.execute(
            update(Job)
            .where(Job.id == exhausted.id)
            .values(lease_until=func.now() - timedelta(seconds=500))
        )
        await db.commit()

    await sample_queue_gauges(session_factory, kinds)

    assert _sample("spanlight_jobs_queued_due", "backup_database") == 2
    backup_age = _sample("spanlight_jobs_queued_oldest_age_seconds", "backup_database")
    assert backup_age is not None and 115 <= backup_age < 130
    assert _sample("spanlight_jobs_queued_due", "other") == 1
    other_age = _sample("spanlight_jobs_queued_oldest_age_seconds", "other")
    assert other_age is not None and 595 <= other_age < 610
    assert _sample("spanlight_jobs_queued_due", "noop") == 0
    assert _sample("spanlight_jobs_reclaimable", "noop") == 1
    noop_lease_age = _sample("spanlight_jobs_reclaimable_oldest_age_seconds", "noop")
    assert noop_lease_age is not None and 5 <= noop_lease_age < 20
    assert _sample("spanlight_jobs_reclaimable", "backup_database") == 0
    assert _sample("spanlight_jobs_reclaimable", "other") == 0

    # Drained: a complete sample resets every known kind to zero.
    async with session_factory() as db:
        await db.execute(update(Job).values(status="done", lease_until=None))
        await db.commit()
    await sample_queue_gauges(session_factory, kinds)
    for name in (
        "spanlight_jobs_queued_due",
        "spanlight_jobs_queued_oldest_age_seconds",
        "spanlight_jobs_reclaimable",
        "spanlight_jobs_reclaimable_oldest_age_seconds",
    ):
        assert [_sample(name, kind) for kind in (*kinds, "other")] == [0, 0, 0]


async def test_queue_gauge_failure_marks_series_unavailable_not_zero(
    session_factory: SessionFactory,
) -> None:
    kinds = {"backup_database"}
    await _enqueue(session_factory, "backup_database", _ago(120))
    await sample_queue_gauges(session_factory, kinds)
    assert _sample("spanlight_jobs_queued_due", "backup_database") == 1
    failed_before = REGISTRY.get_sample_value(
        "spanlight_jobs_queue_samples_total", {"outcome": "failed"}
    )

    async with relation_lock(session_factory):
        await asyncio.wait_for(sample_queue_gauges(session_factory, kinds), timeout=5)
    assert _sample("spanlight_jobs_queued_due", "backup_database") is None
    assert _sample("spanlight_jobs_queued_oldest_age_seconds", "other") is None
    assert (
        REGISTRY.get_sample_value("spanlight_jobs_queue_samples_total", {"outcome": "failed"})
        == (failed_before or 0) + 1
    )

    await sample_queue_gauges(session_factory, kinds)
    assert _sample("spanlight_jobs_queued_due", "backup_database") == 1
