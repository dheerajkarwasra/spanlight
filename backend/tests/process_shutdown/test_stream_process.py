"""Cancelled production gateway streams retain finalization across repeated OS signals."""

import asyncio
import json
import signal
import socket
import time
from contextlib import AsyncExitStack

import httpx
import pytest

from app.config import Settings
from tests.gateway import admission_support
from tests.gateway.admission_support import CHAT, wait_active, world
from tests.gateway.test_stream_lifetime_socket import FloodProvider
from tests.gateway_support import read_gateway_spans
from tests.jobs.registry_support import SessionFactory
from tests.process_shutdown.support import child

pytestmark = pytest.mark.serial


@pytest.mark.parametrize("role", ["api", "gateway"])
@pytest.mark.parametrize("second", [signal.SIGTERM, signal.SIGINT])
async def test_repeated_signal_during_stream_cleanup(
    gateway_settings: Settings,
    session_factory: SessionFactory,
    role: str,
    second: signal.Signals,
) -> None:
    async with AsyncExitStack() as stack:
        w = await world(stack, gateway_settings, False)
        async with child(
            {
                "database_url": gateway_settings.database_url,
                "role": role,
                "case": "stream",
                "close_delay": 0.3,
            }
        ) as run:
            base = await run.ready()
            async with (
                httpx.AsyncClient(trust_env=False) as http,
                http.stream(
                    "POST",
                    base + "/gw/v1/chat/completions",
                    json=CHAT,
                    headers={"Authorization": f"Bearer {w.key}"},
                ) as response,
            ):
                assert response.status_code == 200
                await wait_active(w.provider, 1)
                run.process.send_signal(
                    signal.SIGTERM if second == signal.SIGTERM else signal.SIGINT
                )
                await run.event("finalizer_start")
                run.process.send_signal(second)
                await run.finish()
            names = [item.get("event") for item in run.events]
            print(
                "TASK15D_STREAM "
                + json.dumps({"role": role, "second": int(second), "events": run.events})
            )
            if second == signal.SIGINT:
                assert run.process.returncode == -signal.SIGINT
                assert "pool_start" not in names and "finished" not in names
                assert "process.shutdown_incomplete" in names
            else:
                assert run.process.returncode == -signal.SIGTERM, "".join(run.lines)
                deadlines = [
                    item["deadline"] for item in run.events if item.get("event") == "deadline"
                ]
                assert len(deadlines) == 2 and deadlines[0] == deadlines[1]
                assert names.index("finalizer_end") < names.index("recorder_start")
                assert names.index("recorder_end") < names.index("flush_start")
                assert names.index("flush_end") < names.index("pool_start")
                assert len(await read_gateway_spans(session_factory, w.workspace.project_id)) == 1
            await wait_active(w.provider, 0)


async def test_noncooperative_finalizer_retains_pools_until_external_hard_stop(
    gateway_settings: Settings,
) -> None:
    async with AsyncExitStack() as stack:
        w = await world(stack, gateway_settings, False)
        async with child(
            {
                "database_url": gateway_settings.database_url,
                "role": "api",
                "case": "stream_stuck",
                "budget": [0.8, 0.1, 0.15, 0.15],
            }
        ) as run:
            base = await run.ready()
            async with (
                httpx.AsyncClient(trust_env=False) as http,
                http.stream(
                    "POST",
                    base + "/gw/v1/chat/completions",
                    json=CHAT,
                    headers={"Authorization": f"Bearer {w.key}"},
                ) as response,
            ):
                assert response.status_code == 200
                run.process.send_signal(signal.SIGTERM)
                await run.event("finalizer_start")
                await run.event("process.shutdown_incomplete")
                run.process.send_signal(signal.SIGTERM)
                await asyncio.sleep(0.4)
                assert run.process.returncode is None
                names = [item.get("event") for item in run.events]
                assert "pool_start" not in names and "flush_start" not in names
                assert "finished" not in names
                run.process.kill()
                await run.finish()
                assert run.process.returncode == -signal.SIGKILL
                assert "Application shutdown complete" not in "".join(run.lines)


async def test_real_tcp_backpressure_still_joins_finalizer_before_disposal(
    gateway_settings: Settings,
    session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(admission_support, "HeldProvider", FloodProvider)
    async with AsyncExitStack() as stack:
        w = await world(stack, gateway_settings, False)
        async with child(
            {
                "database_url": gateway_settings.database_url,
                "role": "api",
                "case": "stream_slow",
            }
        ) as run:
            await run.ready()
            port = (await run.event("port"))["port"]
            reader, writer = await asyncio.open_connection("127.0.0.1", port, limit=1024)
            writer.get_extra_info("socket").setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024)
            body = json.dumps(CHAT).encode()
            writer.write(
                (
                    "POST /gw/v1/chat/completions HTTP/1.1\r\nHost: local\r\n"
                    f"Authorization: Bearer {w.key}\r\nContent-Type: application/json\r\n"
                    f"Content-Length: {len(body)}\r\n\r\n"
                ).encode()
                + body
            )
            await writer.drain()
            try:
                async with asyncio.timeout(5):
                    assert b"200 OK" in await reader.readuntil(b"\r\n\r\n")
                    await run.event("send_stalled")
                start = time.monotonic()
                run.process.send_signal(signal.SIGTERM)
                await run.finish()
                elapsed = time.monotonic() - start
                assert run.process.returncode == -signal.SIGTERM
                names = [item.get("event") for item in run.events]
                assert names.index("finalizer_end") < names.index("pool_start")
                assert len(await read_gateway_spans(session_factory, w.workspace.project_id)) == 1
                print(
                    "TASK15D_BACKPRESSURE " + json.dumps({"elapsed": elapsed, "events": run.events})
                )
            finally:
                writer.close()
                await writer.wait_closed()


async def test_cancelled_server_waiter_and_second_waiter_join_same_cleanup(
    gateway_settings: Settings,
    session_factory: SessionFactory,
) -> None:
    async with AsyncExitStack() as stack:
        w = await world(stack, gateway_settings, False)
        async with child(
            {
                "database_url": gateway_settings.database_url,
                "role": "api",
                "case": "stream_waiter",
            }
        ) as run:
            base = await run.ready()
            async with (
                httpx.AsyncClient(trust_env=False) as http,
                http.stream(
                    "POST",
                    base + "/gw/v1/chat/completions",
                    json=CHAT,
                    headers={"Authorization": f"Bearer {w.key}"},
                ) as response,
            ):
                assert response.status_code == 200
                run.process.send_signal(signal.SIGTERM)
                await run.finish()
            names = [item.get("event") for item in run.events]
            assert names.index("shutdown_waiter_cancelled") < names.index("finalizer_end")
            assert names.index("finalizer_end") < names.index("second_shutdown_joined")
            assert names.count("finalizer_end") == 1
            assert len(await read_gateway_spans(session_factory, w.workspace.project_id)) == 1
            assert run.process.returncode == -signal.SIGTERM
