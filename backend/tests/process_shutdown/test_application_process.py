"""Quiet actual-process calibration; sample statistics are not production percentiles."""

import asyncio
import contextlib
import json
import signal
import time

import httpx
import pytest
from sqlalchemy import text

from app.config import Settings
from tests.jobs.registry_support import SessionFactory, seed, snapshot
from tests.process_shutdown.support import child

pytestmark = pytest.mark.serial


@pytest.mark.parametrize(
    ("role", "case"),
    [
        ("api", "idle"),
        ("gateway", "idle"),
        ("api", "sql"),
        ("gateway", "sql"),
        ("api", "startup"),
        ("worker", "startup"),
        ("worker", "short"),
        ("worker", "long"),
    ],
)
async def test_real_process_shutdown(  # noqa: PLR0912 - explicit process-role fault matrix
    settings: Settings,
    session_factory: SessionFactory,
    role: str,
    case: str,
) -> None:
    job = None
    if case in {"short", "long"}:
        job = await seed(session_factory, "shutdown_short" if case == "short" else "create_export")
    async with session_factory() as blocker, blocker.begin():
        await blocker.execute(text("SELECT pg_advisory_xact_lock(150040)"))
        async with child(
            {"database_url": settings.database_url, "role": role, "case": case}
        ) as run:
            request = None
            async with httpx.AsyncClient(trust_env=False, timeout=10) as http:
                pid = None
                if case == "startup":
                    pid = (await run.event("startup_blocked"))["pid"]
                elif role != "worker":
                    base = await run.ready()
                    if case == "sql":
                        request = asyncio.create_task(http.get(base + "/probe"))
                        pid = (await run.event("sql_blocked"))["pid"]
                else:
                    await run.event("handler_started")
                if pid is not None:
                    async with asyncio.timeout(3):
                        while True:
                            async with session_factory() as db:
                                blocked = await db.scalar(
                                    text("SELECT cardinality(pg_blocking_pids(:pid)) > 0"),
                                    {"pid": pid},
                                )
                            if blocked:
                                break
                            await asyncio.sleep(0.005)
                start = time.monotonic()
                run.process.send_signal(signal.SIGTERM)
                await run.finish()
                elapsed = time.monotonic() - start
                if request is not None:
                    with contextlib.suppress(httpx.TransportError):
                        await request
                assert run.process.returncode == -signal.SIGTERM, "".join(run.lines)
                assert elapsed < 2, "".join(run.lines)
                names = [item.get("event") for item in run.events]
                assert "pool_end" in names and "tracing_end" in names
                if case == "sql":
                    assert names.index("request_joined") < names.index("pool_start")
                if case in {"short", "long"}:
                    assert names.index("handler_joined") < names.index("pool_start")
                if pid is not None:
                    async with session_factory() as db:
                        assert (
                            await db.scalar(
                                text("SELECT count(*) FROM pg_stat_activity WHERE pid=:pid"),
                                {"pid": pid},
                            )
                            == 0
                        )
                print(
                    "TASK15D_CALIBRATION "
                    + json.dumps(
                        {
                            "role": role,
                            "case": case,
                            "elapsed": elapsed,
                            "events": run.events,
                        }
                    )
                )
    if job is not None:
        state = await snapshot(session_factory, job)
        assert state["status"].value == ("done" if case == "short" else "queued")
