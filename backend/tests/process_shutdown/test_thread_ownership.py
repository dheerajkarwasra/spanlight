"""A cancelled coroutine is not evidence that an executor operation has finished."""

import asyncio
import contextlib
import os
import signal
import sys

import httpx
import pytest

from tests.process_shutdown.support import Child

pytestmark = pytest.mark.serial


@pytest.mark.parametrize("kind", ["smtp", "argon2", "dns", "httpx_dns", "hidden_executor"])
async def test_live_executor_caller_retained_past_deadline_until_thread_finishes(kind: str) -> None:
    connected = asyncio.Event()
    release = asyncio.Event()

    async def smtp(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        connected.set()
        await release.wait()
        writer.close()
        await writer.wait_closed()

    listener = await asyncio.start_server(smtp, "127.0.0.1", 0)
    read_fd, write_fd = os.pipe()
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "tests.process_shutdown.thread_child",
        kind,
        str(listener.sockets[0].getsockname()[1]),
        str(read_fd),
        pass_fds=(read_fd,),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    os.close(read_fd)
    run = Child(process)
    request = None
    try:
        base = await run.ready()
        async with httpx.AsyncClient(trust_env=False, timeout=5) as http:
            request = asyncio.create_task(http.get(base + "/probe"))
            if kind == "smtp":
                await asyncio.wait_for(connected.wait(), 3)
            else:
                await run.event("thread_started")
            process.send_signal(signal.SIGTERM)
            await asyncio.sleep(0.5)
            names = [item.get("event") for item in run.events]
            assert "process_finished" not in names, "".join(run.lines)
            if kind not in {"httpx_dns", "hidden_executor"}:
                assert "disposed" not in names, "".join(run.lines)
                assert "request_joined" not in names
            assert "process.shutdown_incomplete" in names
            assert process.returncode is None
            process.send_signal(signal.SIGTERM)
            release.set()
            os.write(write_fd, b"x")
            await run.finish()
            with contextlib.suppress(httpx.TransportError):
                await request
            names = [item.get("event") for item in run.events]
            assert names.index("request_joined") < names.index("disposed")
            assert process.returncode == -signal.SIGTERM
            assert not (await run.event("process_finished"))["accounting_complete"]
            # Uvicorn's INFO "Application shutdown complete." is ASGI only; the process line comes
            # after the executor join and carries the accounting outcome.
            finished = await run.event("process.shutdown_finished")
            assert finished["level"] == "info"
            assert finished["accounting_complete"] is False
            assert names.index("disposed") < names.index("process.shutdown_finished")
            if kind != "smtp":
                assert names.index("thread_finished") < names.index("process_finished")
            if kind == "hidden_executor":
                assert (await run.event("loop"))["implementation"].startswith("uvloop")
    except BaseException:
        print("".join(run.lines))
        raise
    finally:
        release.set()
        os.close(write_fd)
        listener.close()
        await listener.wait_closed()
        if process.returncode is None:
            process.kill()
        await run.finish()
        if request is not None:
            await asyncio.gather(request, return_exceptions=True)
