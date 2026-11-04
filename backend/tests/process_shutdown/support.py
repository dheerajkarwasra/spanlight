"""Owned process, output collection and bounded waits for public process events."""

import asyncio
import contextlib
import json
import sys
from collections.abc import AsyncIterator
from typing import Any

import httpx


class Child:
    def __init__(self, process: asyncio.subprocess.Process) -> None:
        self.process = process
        self.events: list[dict[str, Any]] = []
        self.lines: list[str] = []
        self.changed = asyncio.Event()
        self.reader = asyncio.create_task(self._read())

    async def _read(self) -> None:
        assert self.process.stdout is not None
        while line := await self.process.stdout.readline():
            self.lines.append(line.decode())
            if line.startswith(b"{"):
                self.events.append(json.loads(line))
            self.changed.set()
        self.changed.set()

    async def event(self, name: str, deadline_seconds: float = 8) -> dict[str, Any]:
        async with asyncio.timeout(deadline_seconds):
            while True:
                for item in self.events:
                    if item.get("event") == name:
                        return item
                assert not self.reader.done(), "".join(self.lines)
                self.changed.clear()
                await self.changed.wait()

    async def ready(self) -> str:
        port = (await self.event("port"))["port"]
        base = f"http://127.0.0.1:{port}"
        async with asyncio.timeout(8), httpx.AsyncClient(trust_env=False) as http:
            while True:
                try:
                    result = await http.get(base + "/health/live")
                    if result.status_code == 200:
                        return base
                except httpx.TransportError:
                    pass
                assert not self.reader.done(), "".join(self.lines)
                await asyncio.sleep(0.01)

    async def finish(self, deadline_seconds: float = 8) -> None:
        async with asyncio.timeout(deadline_seconds):
            await self.process.wait()
            await self.reader


@contextlib.asynccontextmanager
async def child(data: dict[str, Any]) -> AsyncIterator[Child]:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "tests.process_shutdown.application_child",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    assert process.stdin is not None
    process.stdin.write(json.dumps(data).encode() + b"\n")
    await process.stdin.drain()
    process.stdin.close()
    owned = Child(process)
    try:
        yield owned
    except BaseException:
        print("TASK15D_CHILD_OUTPUT\n" + "".join(owned.lines))
        raise
    finally:
        if process.returncode is None:
            process.kill()
        await owned.finish()
