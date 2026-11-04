"""Failure-path ownership checks; no database or provider transport is involved."""

import asyncio
import socket
import sys
from types import SimpleNamespace
from typing import Any

import pytest
import uvicorn

from tests.integration import network_support


@pytest.fixture(autouse=True)
def clean_database() -> None:
    """These resource lifecycle checks do not use an application or database."""


@pytest.mark.parametrize("cancelled", [False, True])
async def test_hung_sdk_child_is_reaped(cancelled: bool) -> None:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import time; time.sleep(60)",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        task = asyncio.create_task(
            network_support.communicate_sdk(process, b"", deadline_seconds=0.05)
        )
        if cancelled:
            await asyncio.sleep(0.01)
            task.cancel()
        with pytest.raises(asyncio.CancelledError if cancelled else TimeoutError):
            await task
        assert process.returncode is not None
        assert await asyncio.wait_for(process.wait(), 0.1) == process.returncode
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


@pytest.mark.parametrize("failure", ["setup", "startup", "shutdown", "timeout"])
async def test_http_failure_closes_socket_and_settles_task(
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    sockets: list[socket.socket] = []
    tasks: list[asyncio.Task[Any]] = []
    original = AssertionError("original test failure")

    def owned_socket() -> socket.socket:
        sock = socket.socket()
        sockets.append(sock)
        return sock

    class FailingServer(uvicorn.Server):
        def __init__(self, config: uvicorn.Config) -> None:
            if failure == "setup":
                raise RuntimeError("setup failed")
            super().__init__(config)

        async def serve(self, sockets: Any = None) -> None:
            task = asyncio.current_task()
            assert task is not None
            tasks.append(task)
            if failure == "startup":
                raise RuntimeError("startup failed")
            self.started = True
            if failure == "timeout":
                await asyncio.Event().wait()
            else:
                while not self.should_exit:  # noqa: ASYNC110 - emulate Uvicorn exit flag.
                    await asyncio.sleep(0)
                raise RuntimeError("shutdown failed")

    monkeypatch.setattr(network_support, "socket", SimpleNamespace(socket=owned_socket))
    monkeypatch.setattr(uvicorn, "Server", FailingServer)
    expected = RuntimeError if failure in {"setup", "startup"} else AssertionError
    with pytest.raises(expected) as caught:
        async with network_support.serve_http(object(), shutdown_timeout=0.05):
            raise original
    if expected is AssertionError:
        assert caught.value is original
    assert len(sockets) == 1
    assert sockets[0].fileno() == -1
    assert all(task.done() for task in tasks)
