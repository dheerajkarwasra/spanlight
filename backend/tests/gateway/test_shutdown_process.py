"""SIGTERM calibration of 15C components; production server ordering belongs to 15D."""

import asyncio
import json
import signal
import sys
import time
import uuid
from contextlib import AsyncExitStack

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.core.errors import ProblemError
from app.core.idempotency_store import reserve
from app.db.rls import bind_project
from tests.gateway.admission_support import created, wait_active, world
from tests.gateway.loss_support import read_losses
from tests.gateway_support import read_gateway_spans

pytestmark = pytest.mark.serial


@pytest.mark.parametrize("sample", range(3))
async def test_sigterm_joins_stream_and_locked_writer_before_final_flush(
    gateway_settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    sample: int,
) -> None:
    async with AsyncExitStack() as stack:
        w = await world(stack, gateway_settings, False)
        await created(
            w.owner,
            f"/api/v1/orgs/{w.workspace.org_id}/price-overrides",
            {
                "provider": "openai_compatible",
                "model_pattern": "gpt-4.1-mini",
                "input_per_mtok": "1",
                "output_per_mtok": "1",
                "cached_input_per_mtok": "1",
            },
        )
        await created(
            w.owner,
            f"/api/v1/projects/{w.workspace.project_id}/budgets",
            {
                "name": "shutdown",
                "scope": "project",
                "period": "daily",
                "amount_usd": "1",
                "action": "block",
                "channel_ids": [],
            },
        )
        async with session_factory() as blocker, blocker.begin():
            await blocker.execute(text("SELECT pg_advisory_xact_lock(150039)"))
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "tests.gateway.shutdown_process_support",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            assert process.stdin is not None and process.stdout is not None
            process.stdin.write(
                json.dumps(
                    {
                        "database_url": str(gateway_settings.database_url),
                        "project_id": str(w.workspace.project_id),
                    }
                ).encode()
                + b"\n"
            )
            await process.stdin.drain()
            process.stdin.close()
            output = bytearray()
            try:
                async with asyncio.timeout(8):
                    while True:
                        line = await process.stdout.readline()
                        output.extend(line)
                        assert line, output.decode()
                        if line.startswith(b"TASK15C_READY "):
                            ready = json.loads(line.removeprefix(b"TASK15C_READY "))
                            break
                    while True:
                        async with session_factory() as observer:
                            waiting = await observer.scalar(
                                text("SELECT cardinality(pg_blocking_pids(:pid)) > 0"),
                                {"pid": ready["pid"]},
                            )
                        if waiting:
                            break
                        await asyncio.sleep(0.005)
                assert w.provider.active == 1
                signalled = time.monotonic()
                process.send_signal(signal.SIGTERM)
                stdout, stderr = await asyncio.wait_for(process.communicate(), 5)
                elapsed = time.monotonic() - signalled
                output.extend(stdout)
                assert process.returncode == 0, output.decode() + stderr.decode()
                results = [
                    json.loads(line.removeprefix("TASK15C_RESULT "))
                    for line in output.decode().splitlines()
                    if line.startswith("TASK15C_RESULT ")
                ]
                [result] = results
                assert result["lost"] == 2
                print(
                    "TASK15C_CALIBRATION",
                    json.dumps(
                        {
                            "sample": sample,
                            "sigterm_to_exit_s": elapsed,
                            **result,
                        }
                    ),
                )
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.communicate()
        async with session_factory() as observer:
            assert (
                await observer.scalar(
                    text("SELECT count(*) FROM pg_stat_activity WHERE pid=:pid"),
                    {"pid": ready["pid"]},
                )
                == 0
            )
        project = uuid.UUID(w.workspace.project_id)
        assert sum((await read_losses(session_factory, project)).values()) == 2
        assert await read_gateway_spans(session_factory, project) == []
        async with session_factory() as db, db.begin():
            await bind_project(db, project)
            reservations = (
                await db.execute(
                    text("SELECT status, amount_usd, charged_usd FROM budget_reservations")
                )
            ).all()
            [reservation] = reservations
            assert reservation.status == "held"
            assert reservation.amount_usd > 0
            assert reservation.charged_usd is None
        with pytest.raises(ProblemError) as unknown:
            await reserve(session_factory, "user:shutdown", "unknown-call", b"x" * 32)
        assert unknown.value.code == "IDEMPOTENCY_IN_PROGRESS"
        await wait_active(w.provider, 0)
