"""Worker deadlines must release real locks without inventing committed success."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from prometheus_client import REGISTRY
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import SQLAlchemyError

from app.config import Settings
from app.db.models import Job, JobStatus
from app.jobs import queue
from app.jobs.context import TaskContext
from app.jobs.outcome import RetryAfter
from app.jobs.scheduler import period_key, periodic_jobs
from app.jobs.worker import Worker
from tests.integration.network_support import respond, serve_http
from tests.jobs.control_loop_support import (
    SessionFactory,
    enqueue,
    relation_lock,
    row,
    settle,
    wait_for_blocker,
)


async def test_relation_lock_bounds_claim_and_never_starts_uncertain_work(
    session_factory: SessionFactory,
    settings: Settings,
) -> None:
    effect = asyncio.Event()

    async def handler(_: TaskContext, __: dict[str, Any]) -> None:
        effect.set()

    job_id = await enqueue(session_factory)
    worker = Worker(session_factory, settings, handlers={"effect": handler})
    async with relation_lock(session_factory) as pid:
        task = asyncio.create_task(worker.run_once())
        try:
            await wait_for_blocker(session_factory, pid)
            done, _ = await asyncio.wait({task}, timeout=1.5)
            assert done, "claim remained blocked past its operation envelope"
            with pytest.raises((TimeoutError, SQLAlchemyError)):
                await task
            assert not effect.is_set()
        finally:
            await settle(task)
    assert await worker.run_once()
    assert effect.is_set()
    assert (await row(session_factory, job_id)).status is JobStatus.DONE


async def test_failed_schedule_still_claims_queued_work_and_retries_schedule(
    session_factory: SessionFactory,
    settings: Settings,
) -> None:
    effect = asyncio.Event()
    stop = asyncio.Event()

    async def handler(_: TaskContext, __: dict[str, Any]) -> None:
        effect.set()
        stop.set()

    await enqueue(session_factory)
    worker = Worker(session_factory, settings, handlers={"effect": handler})
    item = periodic_jobs(settings)[0]
    now = datetime.now(UTC)
    async with session_factory() as holder:
        await queue.enqueue(holder, item.kind, dedupe_key=period_key(item, now))
        pid = await holder.scalar(text("SELECT pg_backend_pid()"))
        assert isinstance(pid, int)
        task = asyncio.create_task(worker.run_forever(stop))
        try:
            await wait_for_blocker(session_factory, pid)
            done, _ = await asyncio.wait({task}, timeout=1.5)
            assert effect.is_set(), "failed schedule suppressed unrelated queued work"
            assert done
            await task
        finally:
            stop.set()
            await settle(task)
            await holder.rollback()
    await worker.schedule_if_due()
    async with session_factory() as db:
        keys = (await db.scalars(select(Job.dedupe_key).where(Job.kind == item.kind))).all()
    assert period_key(item, now) in keys, "failed schedule advanced last-scheduled timestamp"


@pytest.mark.parametrize("terminal", ["complete", "fail", "retry"])
async def test_terminal_uncertainty_preserves_effect_lease_and_fence(
    session_factory: SessionFactory,
    settings: Settings,
    terminal: str,
) -> None:
    done_before = (
        REGISTRY.get_sample_value(
            "spanlight_jobs_finished_total",
            {
                "kind": "effect",
                "outcome": "done",
            },
        )
        or 0
    )
    failures_before = (
        REGISTRY.get_sample_value(
            "spanlight_worker_operation_failures_total",
            {
                "operation": terminal,
            },
        )
        or 0
    )
    job_id = await enqueue(session_factory)
    effects: list[bytes] = []
    claims: list[queue.ClaimedJob] = []
    second_started = asyncio.Event()
    second_release = asyncio.Event()
    async with session_factory() as holder:

        async def receiver(scope: Any, receive: Any, send: Any) -> None:
            effects.append(b"delivered")
            if len(effects) == 1:
                await holder.execute(select(Job).where(Job.id == job_id).with_for_update())
            await respond(send, b"{}")

        async with serve_http(receiver) as url:

            async def handler(context: TaskContext, _: dict[str, Any]) -> Any:
                claims.append(context.job)
                async with httpx.AsyncClient(trust_env=False) as client:
                    response = await client.post(url, content=b"delivery")
                    assert response.status_code == 200
                if len(effects) == 2:
                    second_started.set()
                    await second_release.wait()
                if len(effects) == 1 and terminal == "fail":
                    raise RuntimeError("delivery acknowledgment failed")
                if len(effects) == 1 and terminal == "retry":
                    return RetryAfter(retry_after_s=1, reason="delivery uncertain")
                return None

            worker = Worker(session_factory, settings, handlers={"effect": handler})
            task = asyncio.create_task(worker.run_once())
            try:
                done, _ = await asyncio.wait({task}, timeout=1.5)
                assert done, "terminal persistence remained blocked past its operation envelope"
                with pytest.raises((TimeoutError, SQLAlchemyError)):
                    await task
                assert (
                    REGISTRY.get_sample_value(
                        "spanlight_jobs_finished_total",
                        {
                            "kind": "effect",
                            "outcome": "done",
                        },
                    )
                    or 0
                ) == done_before
                assert (
                    REGISTRY.get_sample_value(
                        "spanlight_worker_operation_failures_total",
                        {
                            "operation": terminal,
                        },
                    )
                    == failures_before + 1
                )
                assert effects == [b"delivered"]
                uncertain = await row(session_factory, job_id)
                assert (uncertain.status, uncertain.attempts, uncertain.fence) == (
                    JobStatus.RUNNING,
                    1,
                    1,
                )
                assert uncertain.outcome is None and uncertain.lease_until is not None
            finally:
                await settle(task)
                await holder.rollback()
            async with session_factory() as db:
                await db.execute(
                    update(Job)
                    .where(Job.id == job_id)
                    .values(lease_until=func.now() - timedelta(seconds=1))
                )
                await db.commit()
            second = Worker(session_factory, settings, handlers={"effect": handler})
            second_task = asyncio.create_task(second.run_once())
            try:
                await asyncio.wait_for(second_started.wait(), timeout=2)
                reclaimed = await row(session_factory, job_id)
                assert (reclaimed.status, reclaimed.attempts, reclaimed.fence) == (
                    JobStatus.RUNNING,
                    2,
                    2,
                )
                async with session_factory() as db:
                    with pytest.raises(queue.LeaseLostError):
                        await queue.complete(db, claims[0])
                    with pytest.raises(queue.LeaseLostError):
                        await queue.fail(db, claims[0], "stale")
                    with pytest.raises(queue.LeaseLostError):
                        await queue.extend_lease(db, claims[0])
                second_release.set()
                assert await asyncio.wait_for(second_task, timeout=2)
                assert effects == [b"delivered", b"delivered"]
                assert (await row(session_factory, job_id)).status is JobStatus.DONE
            finally:
                second_release.set()
                await settle(second_task)


async def test_idle_successful_poll_exports_progress_not_stall(
    session_factory: SessionFactory,
    settings: Settings,
) -> None:
    worker = Worker(session_factory, settings, handlers={})
    await worker.schedule_if_due()
    async with session_factory() as db:
        await db.execute(text("DELETE FROM jobs"))
        await db.commit()
    stop = asyncio.Event()
    task = asyncio.create_task(worker.run_forever(stop))
    try:
        await asyncio.sleep(0.2)
        assert REGISTRY.get_sample_value("spanlight_worker_short_loop_stalled") == 0
        age = REGISTRY.get_sample_value("spanlight_worker_claim_poll_age_seconds")
        assert age is not None and 0 <= age < 1
        assert (
            REGISTRY.get_sample_value("spanlight_worker_short_loop_phase", {"phase": "idle"}) == 1
        )
    finally:
        stop.set()
        await settle(task)


async def test_long_terminal_work_never_refreshes_or_replaces_short_progress(
    session_factory: SessionFactory,
    settings: Settings,
) -> None:
    release_long = asyncio.Event()
    release_short = asyncio.Event()
    short_started = asyncio.Event()

    async def long_handler(context: TaskContext, _: dict[str, Any]) -> None:
        await context.heartbeat()
        await release_long.wait()

    async def short_handler(_: TaskContext, __: dict[str, Any]) -> None:
        short_started.set()
        await release_short.wait()

    worker = Worker(
        session_factory,
        settings,
        handlers={
            "backup_database": long_handler,
            "effect": short_handler,
        },
    )
    long_id = await enqueue(session_factory, "backup_database")
    assert await worker.run_once()
    await enqueue(session_factory)
    task = asyncio.create_task(worker.run_once())
    try:
        await asyncio.wait_for(short_started.wait(), timeout=2)
        await asyncio.sleep(0.15)
        release_long.set()
        async with asyncio.timeout(2):
            while True:
                if (await row(session_factory, long_id)).status is JobStatus.DONE:
                    break
                await asyncio.sleep(0.002)
        worker.progress.report()
        assert (
            REGISTRY.get_sample_value(
                "spanlight_worker_short_loop_phase", {"phase": "short_execution"}
            )
            == 1
        )
        assert REGISTRY.get_sample_value("spanlight_worker_claim_poll_age_seconds") >= 0.15
    finally:
        release_long.set()
        release_short.set()
        await settle(task)
        # Drain the public loop so its completed long slot is observed and freed.
        await worker.run_once()
