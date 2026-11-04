"""Real disposable server; protocol events expose shutdown ordering to its parent."""

import asyncio
import json
import os
import socket
import time
from typing import Any

import uvicorn

from app.process_shutdown import ShutdownBudget
from app.server import ShutdownServer


def event(name: str, **values: object) -> None:
    print(json.dumps({"event": name, "time": time.monotonic(), **values}), flush=True)


async def application(scope: Any, receive: Any, send: Any) -> None:
    if scope["type"] == "lifespan":
        await receive()
        event("startup")
        if os.environ.get("CASE") == "startup":
            try:
                await asyncio.Event().wait()
            finally:
                event("startup_joined")
        await send({"type": "lifespan.startup.complete"})
        await receive()
        event("disposed")
        await send({"type": "lifespan.shutdown.complete"})
        return
    event("request")
    try:
        await asyncio.Event().wait()
    finally:
        event("cancelled")
        await asyncio.sleep(0.2)
        event("request_joined")


def main() -> None:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    event("port", port=sock.getsockname()[1])

    server = ShutdownServer
    server(
        uvicorn.Config(
            application,
            lifespan="on",
            timeout_graceful_shutdown=0.05,
            log_level="info",
            loop=os.environ.get("LOOP", "auto"),
        ),
        owns_executor=True,
        budget=ShutdownBudget(0.6, 0.05, 0.1, 0.1)
        if os.environ.get("CASE") == "late_cleanup"
        else ShutdownBudget(2, 0.05, 0.2, 0.2),
    ).run(sockets=[sock])


if __name__ == "__main__":
    main()
