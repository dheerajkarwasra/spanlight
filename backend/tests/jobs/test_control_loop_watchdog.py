"""Heartbeat liveness cannot conceal stalled short-loop polling."""

import asyncio
import logging
from typing import Any

import pytest
from prometheus_client import REGISTRY
from sqlalchemy import select, text

from app.config import Settings
from app.db.models import WorkerHeartbeat
from app.jobs.context import TaskContext
from app.jobs.worker import Worker
from app.jobs.worker_heartbeat import beat, run_heartbeat
from tests.gateway_support import logged
from tests.jobs.control_loop_support import SessionFactory, enqueue, relation_lock, settle


async def test_stalled_claims_warn_while_real_heartbeat_writes_continue(
    session_factory: SessionFactory,
    settings: Settings,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)
    worker = Worker(session_factory, settings, handlers={})
    stop = asyncio.Event()
    async with relation_lock(session_factory):
        task = asyncio.create_task(worker.run_forever(stop))
        try:
            async with asyncio.timeout(2):
                first_seen = None
                while first_seen is None:
                    async with session_factory() as db:
                        first_seen = await db.scalar(
                            select(WorkerHeartbeat.last_seen_at).where(
                                WorkerHeartbeat.worker_id == worker.worker_id
                            )
                        )
                    await asyncio.sleep(0.01)
            async with asyncio.timeout(78):
                while True:
                    if REGISTRY.get_sample_value("spanlight_worker_short_loop_stalled") == 1:
                        break
                    await asyncio.sleep(0.1)
            async with session_factory() as db:
                heartbeat = await db.get(WorkerHeartbeat, worker.worker_id)
                assert heartbeat is not None
                assert (heartbeat.last_seen_at - first_seen).total_seconds() >= 60
            warnings = logged(caplog, "worker_short_loop_stalled")
            assert len(warnings) == 1
            assert warnings[0]["claim_poll_age_seconds"] > 65
            assert REGISTRY.get_sample_value("spanlight_worker_claim_poll_age_seconds") > 65
            assert (
                REGISTRY.get_sample_value(
                    "spanlight_worker_operation_failures_total", {"operation": "claim"}
                )
                > 1
            )
        finally:
            stop.set()
            await asyncio.wait_for(task, timeout=3)
    # Successful empty polling clears the stale evidence, independently of queued work.
    async with session_factory() as db:
        await db.execute(text("DELETE FROM jobs"))
        await db.commit()
    assert not await worker.run_once()
    worker.progress.report()
    assert REGISTRY.get_sample_value("spanlight_worker_short_loop_stalled") == 0


async def test_legitimate_50_second_handler_and_idle_polling_do_not_stall(
    session_factory: SessionFactory,
    settings: Settings,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)
    started = asyncio.Event()
    release = asyncio.Event()
    stop = asyncio.Event()

    async def handler(_: TaskContext, __: dict[str, Any]) -> None:
        started.set()
        await release.wait()
        stop.set()

    worker = Worker(session_factory, settings, handlers={"effect": handler})
    await worker.schedule_if_due()
    async with session_factory() as db:
        await db.execute(text("DELETE FROM jobs"))
        await db.commit()
    await enqueue(session_factory)
    task = asyncio.create_task(worker.run_forever(stop))
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        # Retain the real 50-second allowance: sample near its end without replacing clocks.
        await asyncio.sleep(49)
        assert not logged(caplog, "worker_short_loop_stalled")
        assert REGISTRY.get_sample_value("spanlight_worker_short_loop_stalled") == 0
        release.set()
        await asyncio.wait_for(task, timeout=3)
        assert not logged(caplog, "worker_short_loop_stalled")
        assert not await worker.run_once()
        await asyncio.sleep(2.1)
        assert not await worker.run_once()
        worker.progress.report()
        assert REGISTRY.get_sample_value("spanlight_worker_short_loop_stalled") == 0
    finally:
        release.set()
        stop.set()
        await settle(task)


async def test_watchdog_callback_continues_after_real_heartbeat_write_timeout(
    session_factory: SessionFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)
    await beat(session_factory, "blocked-heartbeat", "test")
    stop = asyncio.Event()
    second_report = asyncio.Event()
    reports: list[str] = []

    def report() -> None:
        reports.append("checked")
        if len(reports) == 2:
            stop.set()
            second_report.set()

    async with session_factory() as holder:
        await holder.execute(
            select(WorkerHeartbeat)
            .where(WorkerHeartbeat.worker_id == "blocked-heartbeat")
            .with_for_update()
        )
        task = asyncio.create_task(
            run_heartbeat(
                session_factory,
                stop,
                "blocked-heartbeat",
                interval=0.01,
                report_progress=report,
            )
        )
        try:
            await asyncio.wait_for(second_report.wait(), timeout=7)
            assert reports == ["checked", "checked"]
            assert len(logged(caplog, "worker_heartbeat_failed")) == 1
        finally:
            stop.set()
            await holder.rollback()
            await asyncio.wait_for(task, timeout=2)
