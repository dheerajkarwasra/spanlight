"""A process flush blocked by SQL or unavailable PostgreSQL must not claim complete accounting."""

import json
import signal
import socket
import time

import httpx
import pytest
from sqlalchemy import text

from app.config import Settings
from tests.gateway.loss_support import two_projects
from tests.jobs.registry_support import SessionFactory
from tests.process_shutdown.support import child

pytestmark = pytest.mark.serial


@pytest.mark.parametrize("unavailable", [False, True])
async def test_process_reports_incomplete_final_loss_flush(
    settings: Settings,
    session_factory: SessionFactory,
    unavailable: bool,
) -> None:
    project_id, _ = await two_projects(session_factory)
    url = settings.database_url
    if unavailable:
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        url = f"postgresql+psycopg://unused:unused@127.0.0.1:{port}/spanlight_test_unavailable"
    async with session_factory() as blocker, blocker.begin():
        await blocker.execute(text("LOCK TABLE telemetry_losses_hourly IN ACCESS EXCLUSIVE MODE"))
        async with child(
            {
                "database_url": url,
                "role": "api",
                "case": "loss_flush",
                "project_id": str(project_id),
            }
        ) as run:
            base = await run.ready()
            async with httpx.AsyncClient(trust_env=False) as http:
                assert (await http.get(base + "/probe")).status_code == 200
            start = time.monotonic()
            run.process.send_signal(signal.SIGTERM)
            await run.finish()
            elapsed = time.monotonic() - start
            assert run.process.returncode == -signal.SIGTERM, "".join(run.lines)
            assert not (await run.event("finished"))["accounting_complete"]
            failures = [
                item for item in run.events if item.get("event") == "process.shutdown_incomplete"
            ]
            assert any(item.get("phase") == "loss_flush" for item in failures)
            async with session_factory() as db:
                assert (
                    await db.scalar(
                        text("SELECT count(*) FROM pg_stat_activity WHERE application_name=:name"),
                        {"name": f"task15d_{run.process.pid}"},
                    )
                    == 0
                )
            print(
                "TASK15D_LOSS "
                + json.dumps(
                    {
                        "unavailable": unavailable,
                        "elapsed": elapsed,
                        "events": run.events,
                    }
                )
            )
