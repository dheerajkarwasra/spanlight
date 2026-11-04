"""Startup/close failures and claimed-worker terminal SQL remain explicit outcomes."""

import signal

import pytest
from sqlalchemy import text

from app.config import Settings
from tests.jobs.registry_support import SessionFactory, seed, snapshot
from tests.process_shutdown.support import child

pytestmark = pytest.mark.serial


async def test_startup_failure_reverse_closes_acquired_resources(settings: Settings) -> None:
    async with child(
        {
            "database_url": settings.database_url,
            "role": "api",
            "case": "startup_failure",
        }
    ) as run:
        await run.finish()
        assert run.process.returncode == 3, "".join(run.lines)
        names = [item.get("event") for item in run.events]
        assert names.count("pool_end") == 3
        assert names.index("channel_end") < names.index("pool_start")
        assert max(i for i, name in enumerate(names) if name == "pool_end") < names.index(
            "tracing_start"
        )
        assert names.index("tracing_end") < names.index("executor_start")
        assert names[-1] == "executor_end"
        assert "finished" not in names


async def test_close_failure_does_not_skip_later_safe_callbacks(settings: Settings) -> None:
    async with child(
        {
            "database_url": settings.database_url,
            "role": "api",
            "case": "close_failure",
        }
    ) as run:
        await run.ready()
        run.process.send_signal(signal.SIGTERM)
        await run.finish()
        names = [item.get("event") for item in run.events]
        assert names.count("pool_end") == 3 and "tracing_end" in names
        assert "process.shutdown_incomplete" in names
        assert not (await run.event("finished"))["accounting_complete"]
        assert run.process.returncode == -signal.SIGTERM


async def test_claim_returning_after_stop_never_starts_handler(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    job = await seed(session_factory, "shutdown_short")
    async with child(
        {
            "database_url": settings.database_url,
            "role": "worker",
            "case": "postclaim",
        }
    ) as run:
        await run.event("claimed")
        run.process.send_signal(signal.SIGTERM)
        await run.finish()
        assert "handler_started" not in [item.get("event") for item in run.events]
        state = await snapshot(session_factory, job)
        assert state["status"].value == "queued"
        assert state["attempts"] == 1
        assert run.process.returncode == -signal.SIGTERM


@pytest.mark.parametrize("kind", ["shutdown_short", "create_export"])
async def test_locked_terminal_write_keeps_lease_uncertain_and_joins_before_disposal(
    settings: Settings,
    session_factory: SessionFactory,
    kind: str,
) -> None:
    job = await seed(session_factory, kind)
    async with child(
        {
            "database_url": settings.database_url,
            "role": "worker",
            "case": "terminal",
            "handler_seconds": 0.3,
        }
    ) as run:
        await run.event("handler_started")
        async with session_factory() as db, db.begin():
            await db.execute(text("SELECT id FROM jobs WHERE id=:id FOR UPDATE"), {"id": job})
            run.process.send_signal(signal.SIGTERM)
            await run.finish()
            state = await snapshot(session_factory, job)
            assert state["status"].value == "running"
            assert state["attempts"] == 1
        names = [item.get("event") for item in run.events]
        assert "job_done" not in names
        assert "worker_operation_failed" in names
        assert names.index("handler_joined") < names.index("pool_start")
        assert run.process.returncode == -signal.SIGTERM, "".join(run.lines)


async def test_cancelled_terminal_ack_reports_uncertainty_without_replacement_failure(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    job = await seed(session_factory, "shutdown_short")
    async with child(
        {
            "database_url": settings.database_url,
            "role": "worker",
            "case": "terminal_ack",
        }
    ) as run:
        await run.event("terminal_committed")
        run.process.send_signal(signal.SIGTERM)
        await run.finish()
        failures = [item for item in run.events if item.get("event") == "worker_operation_failed"]
        assert len(failures) == 1
        assert failures[0]["operation"] == "complete"
        assert failures[0]["error_type"] == "CancelledError"
        assert failures[0]["persistence_uncertain"]
        state = await snapshot(session_factory, job)
        assert state["status"].value == "done"
        assert state["attempts"] == 1
        assert "job_done" not in [item.get("event") for item in run.events]
        assert run.process.returncode == -signal.SIGTERM


async def test_worker_close_failure_reports_incomplete_and_still_closes_tracing(
    settings: Settings,
) -> None:
    async with child(
        {"database_url": settings.database_url, "role": "worker", "case": "worker_close_failure"}
    ) as run:
        await run.event("worker_ready")
        run.process.send_signal(signal.SIGTERM)
        await run.finish()
        names = [item.get("event") for item in run.events]
        assert "pool_end" in names and "tracing_end" in names
        assert "process.shutdown_incomplete" in names
        assert run.process.returncode != 0


@pytest.mark.parametrize("case", ["worker_tracing_failure", "worker_price_failure"])
async def test_worker_startup_failure_closes_only_acquired_resources(
    settings: Settings, case: str
) -> None:
    async with child(
        {"database_url": settings.database_url, "role": "worker", "case": case}
    ) as run:
        await run.finish()
        names = [item.get("event") for item in run.events]
        assert names.count("pool_end") == 1
        assert ("tracing_end" in names) == (case == "worker_price_failure")
        assert run.process.returncode == 1
