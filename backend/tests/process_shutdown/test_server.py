"""Observe real signals, sockets, and producer/disposal ordering across a process boundary."""

import asyncio
import json
import os
import signal
import sys
from typing import Any

import pytest

pytestmark = pytest.mark.serial


@pytest.mark.parametrize("late_cleanup", [False, True])
async def test_cancelled_request_finishes_before_lifespan_disposal(  # noqa: PLR0912 - child protocol
    late_cleanup: bool,
) -> None:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "tests.process_shutdown.server_child",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env={**os.environ, "CASE": "late_cleanup" if late_cleanup else "normal"},
    )
    assert process.stdout is not None
    events: list[dict[str, Any]] = []
    output: list[bytes] = []
    writer = None
    try:
        async with asyncio.timeout(8):
            while True:
                line = await process.stdout.readline()
                assert line, "server exited before request"
                if line.startswith(b"{"):
                    item = json.loads(line)
                    events.append(item)
                    if item["event"] == "port":
                        port = item["port"]
                    if item["event"] == "startup":
                        # Wait for the actual listener, not just lifespan startup.
                        while True:
                            try:
                                _, writer = await asyncio.open_connection("127.0.0.1", port)
                                break
                            except ConnectionRefusedError:
                                await asyncio.sleep(0.005)
                        writer.write(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
                        await writer.drain()
                    if item["event"] == "request":
                        process.send_signal(signal.SIGTERM)
                        break
            while line := await process.stdout.readline():
                output.append(line)
                if line.startswith(b"{"):
                    events.append(json.loads(line))
            await process.wait()
        names = [item["event"] for item in events]
        print(json.dumps(events))
        assert names.index("request_joined") < names.index("disposed")
        assert process.returncode == -signal.SIGTERM
        if late_cleanup:
            assert b"process.shutdown_incomplete" in b"".join(output)
    finally:
        if writer is not None:
            writer.close()
            await writer.wait_closed()
        if process.returncode is None:
            process.kill()
        await process.wait()


async def test_signal_wakes_blocked_startup_on_plain_asyncio() -> None:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "tests.process_shutdown.server_child",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env={**os.environ, "LOOP": "asyncio", "CASE": "startup"},
    )
    assert process.stdout is not None
    try:
        async with asyncio.timeout(5):
            while True:
                line = await process.stdout.readline()
                assert line
                if line.startswith(b"{") and json.loads(line)["event"] == "startup":
                    break
        process.send_signal(signal.SIGTERM)
        async with asyncio.timeout(1):
            output, _ = await process.communicate()
        assert b"startup_joined" in output
        assert process.returncode == -signal.SIGTERM
    finally:
        if process.returncode is None:
            process.kill()
        await process.wait()
