"""A termination between scheduling and claiming cannot admit another handler."""

import asyncio
from typing import Any

import pytest

from app.config import Settings
from app.jobs.context import TaskContext
from app.jobs.metrics_server import serve_metrics
from app.jobs.worker import Worker
from app.jobs.worker_lifecycle import run_worker
from app.process_shutdown import ShutdownBudget
from tests.jobs.registry_support import SessionFactory, seed, snapshot

pytestmark = pytest.mark.serial


async def test_stop_during_schedule_preserves_unclaimed_job_and_can_be_reused(
    session_factory: SessionFactory,
    gateway_settings: Settings,
) -> None:
    settings = gateway_settings  # with CREDENTIALS_KEYS, so no unrelated start-up warning
    stop = asyncio.Event()
    called = []

    async def handler(_: TaskContext, payload: dict[str, Any]) -> None:
        called.append(payload)

    class StoppingWorker(Worker):
        async def schedule_if_due(self) -> None:
            await super().schedule_if_due()
            stop.set()

    job = await seed(session_factory, "termination-test")
    before = await snapshot(session_factory, job)
    worker = StoppingWorker(session_factory, settings, handlers={"termination-test": handler})
    await worker.run_forever(stop)
    assert called == []
    assert await snapshot(session_factory, job) == before
    assert await worker.run_once()
    assert called == [{}]
    assert (await snapshot(session_factory, job))["status"].value == "done"


async def test_embedded_worker_keeps_hosts_default_executor(gateway_settings: Settings) -> None:
    settings = gateway_settings  # with CREDENTIALS_KEYS, so no unrelated start-up warning

    class FinishedWorker(Worker):
        async def run_forever(self, stop: asyncio.Event) -> None:
            stop.set()
            await super().run_forever(stop)

    await run_worker(settings, ShutdownBudget(1, 0.1, 0.1, 0.1), FinishedWorker, serve_metrics)
    assert await asyncio.to_thread(str, 42) == "42"
