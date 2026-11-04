"""Current-source quiet samples; the earlier paired baseline is retained in task evidence."""

import asyncio
import json
import signal
import socket
import time

import pytest
from sqlalchemy import text

from app.config import Settings
from tests.jobs.registry_support import SessionFactory, seed, snapshot
from tests.process_shutdown.support import child

pytestmark = pytest.mark.serial


@pytest.mark.parametrize("role", ["api", "gateway"])
@pytest.mark.parametrize("sample", range(3))
async def test_idle_candidate(
    settings: Settings,
    session_factory: SessionFactory,
    role: str,
    sample: int,
) -> None:
    async with child(
        {
            "database_url": settings.database_url,
            "role": role,
            "case": "idle",
            "baseline": False,
            "budget": [60, 50, 3, 2],
        }
    ) as run:
        base = await run.ready()
        port = int(base.rsplit(":", 1)[1])
        start = time.monotonic()
        run.process.send_signal(signal.SIGTERM)
        async with asyncio.timeout(2):
            while True:
                # A raw probe: the closing listener can reset an accepted connection, which
                # breaks a stream transport's TCP_NODELAY setup on macOS and leaks its socket.
                probe = socket.socket()
                probe.setblocking(False)
                try:
                    await asyncio.get_running_loop().sock_connect(probe, ("127.0.0.1", port))
                except ConnectionRefusedError:
                    break
                except ConnectionResetError:
                    pass
                finally:
                    probe.close()
                await asyncio.sleep(0.005)
        ingress_refused = time.monotonic() - start
        await run.finish()
        elapsed = time.monotonic() - start
        assert run.process.returncode == -signal.SIGTERM, "".join(run.lines)
        assert elapsed < 2
        async with session_factory() as db:
            assert (
                await db.scalar(
                    text("SELECT count(*) FROM pg_stat_activity WHERE application_name=:name"),
                    {"name": f"task15d_{run.process.pid}"},
                )
                == 0
            )
        print(
            "TASK15D_CALIBRATION "
            + json.dumps(
                {
                    "baseline": False,
                    "role": role,
                    "case": "idle",
                    "sample": sample,
                    "ingress_refused_elapsed": ingress_refused,
                    "elapsed": elapsed,
                    "events": run.events,
                }
            )
        )


async def test_full_short_work_allowance(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    job = await seed(session_factory, "shutdown_short")
    async with child(
        {
            "database_url": settings.database_url,
            "role": "worker",
            "case": "short",
            "baseline": False,
            "handler_seconds": 49,
            "budget": [60, 50, 3, 2],
        }
    ) as run:
        await run.event("handler_started")
        start = time.monotonic()
        run.process.send_signal(signal.SIGTERM)
        await run.finish(deadline_seconds=55)
        elapsed = time.monotonic() - start
        assert run.process.returncode == -signal.SIGTERM, "".join(run.lines)
        assert elapsed < 52
        assert (await snapshot(session_factory, job))["status"].value == "done"
        print(
            "TASK15D_CALIBRATION "
            + json.dumps(
                {
                    "baseline": False,
                    "role": "worker",
                    "case": "full_short",
                    "elapsed": elapsed,
                    "events": run.events,
                }
            )
        )
