"""Test-local real HTTP servers. Request payloads stay in memory, never in logs."""

import asyncio
import socket
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from typing import Any

import httpx
import uvicorn


@asynccontextmanager
async def running_application(application: Any) -> AsyncIterator[Any]:
    lifecycle = application.router.lifespan_context(application)
    await lifecycle.__aenter__()
    try:
        yield application
    finally:
        # The app lifespan's cleanup follows yield; exit normally even if an assertion failed.
        await lifecycle.__aexit__(None, None, None)


@asynccontextmanager
async def serve_http(application: Any, *, shutdown_timeout: float = 10) -> AsyncIterator[str]:
    sock = socket.socket()
    server: uvicorn.Server | None = None
    task: asyncio.Task[None] | None = None
    failure: BaseException | None = None
    try:
        sock.bind(("127.0.0.1", 0))
        sock.listen(128)
        sock.setblocking(False)
        server = uvicorn.Server(
            uvicorn.Config(
                application,
                log_level="critical",
                access_log=False,
                lifespan="off",
            )
        )
        task = asyncio.create_task(server.serve(sockets=[sock]))
        async with asyncio.timeout(10):
            while not server.started:
                if task.done():
                    await task
                    raise RuntimeError("HTTP server stopped before startup")
                await asyncio.sleep(0.01)
        yield f"http://127.0.0.1:{sock.getsockname()[1]}"
    except BaseException as error:
        failure = error
        raise
    finally:
        try:
            if server is not None:
                server.should_exit = True
            if task is not None:
                # wait_for cancels and settles the owned task when shutdown times out.
                await asyncio.wait_for(task, shutdown_timeout)
        except BaseException as error:
            if failure is None:
                raise
            failure.add_note(f"HTTP server cleanup failed: {type(error).__name__}")
        finally:
            sock.close()


async def communicate_sdk(
    process: asyncio.subprocess.Process,
    payload: bytes,
    *,
    deadline_seconds: float = 40,
) -> tuple[bytes, bytes]:
    try:
        return await asyncio.wait_for(process.communicate(payload), deadline_seconds)
    except BaseException as failure:
        try:
            if process.returncode is None:
                with suppress(ProcessLookupError):
                    process.kill()
            await asyncio.wait_for(process.wait(), 5)
        except BaseException as error:  # noqa: BLE001 - preserve the original SDK failure.
            failure.add_note(f"SDK child cleanup failed: {type(error).__name__}")
        raise


async def body_from(receive: Any) -> bytes:
    body: bytes = b""
    while True:
        event = await receive()
        body += event.get("body", b"")
        if not event.get("more_body", False):
            return body


async def respond(send: Any, body: bytes, status: int = 200) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json")],
        }
    )
    await send({"type": "http.response.body", "body": body})


class CommittedWriteFaultProxy:
    """Forward and await backend commit, then corrupt only the first ACK for each path."""

    def __init__(
        self, backend_url: str, committed: Callable[[str, bytes], Awaitable[None]]
    ) -> None:
        self.backend_url = backend_url
        self.committed = committed
        self.requests: dict[str, list[bytes]] = {}
        self.acknowledgments: dict[str, list[dict[str, Any]]] = {}

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        path = scope["path"]
        body = await body_from(receive)
        attempts = self.requests.setdefault(path, [])
        attempts.append(body)
        headers = {
            k.decode(): v.decode()
            for k, v in scope["headers"]
            if k.lower() in {b"authorization", b"content-type", b"content-encoding"}
        }
        async with httpx.AsyncClient(trust_env=False) as client:
            response = await client.post(self.backend_url + path, content=body, headers=headers)
        assert response.status_code == 200
        self.acknowledgments.setdefault(path, []).append(response.json())
        if len(attempts) == 1:
            await self.committed(path, body)
        # A separate authorized read observed the commit before the SDK sees ambiguity.
        await respond(
            send, b"<html>lost acknowledgment</html>" if len(attempts) == 1 else response.content
        )
