"""Real default/Argon2 executor work held across request cancellation and process deadline."""

import asyncio
import os
import socket
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx
import uvicorn

from app.core.egress import system_resolver
from app.core.logging import configure_logging
from app.core.security import _run_hashing
from app.email.message import EmailMessage
from app.email.providers.smtp import SmtpEmailSender
from app.process_shutdown import ShutdownBudget
from app.server import ShutdownServer
from tests.process_shutdown.server_child import event


async def application(scope: Any, receive: Any, send: Any) -> None:
    if scope["type"] == "lifespan":
        await receive()
        await send({"type": "lifespan.startup.complete"})
        await receive()
        event("disposed")
        await send({"type": "lifespan.shutdown.complete"})
        return
    if scope["path"] == "/health/live":
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})
        return
    try:
        if sys.argv[1] in {"httpx_dns", "hidden_executor"}:

            def held_dns(*args: Any, **kwargs: Any) -> Any:
                answer = original_dns(*args, **kwargs)
                event("thread_started")
                os.read(int(sys.argv[3]), 1)
                event("thread_finished")
                return answer

            def held_external_work() -> None:
                event("thread_started")
                os.read(int(sys.argv[3]), 1)
                event("thread_finished")

            original_dns = socket.getaddrinfo
            if sys.argv[1] == "httpx_dns":
                socket.getaddrinfo = held_dns
            try:
                async with httpx.AsyncClient(trust_env=False) as http:
                    await http.get(f"http://localhost:{scope['server'][1]}/health/live")
                if sys.argv[1] == "hidden_executor":
                    await asyncio.get_running_loop().run_in_executor(None, held_external_work)
            finally:
                socket.getaddrinfo = original_dns
        elif sys.argv[1] == "smtp":
            sender = SmtpEmailSender(
                host="127.0.0.1",
                port=int(sys.argv[2]),
                from_address="from@example.test",
                starttls=False,
            )
            await sender.send(EmailMessage(to="to@example.test", subject="test", text="test"))
        else:

            def held_hash() -> str:
                event("thread_started")
                os.read(int(sys.argv[3]), 1)
                event("thread_finished")
                return "test value"

            if sys.argv[1] == "dns":
                loop = asyncio.get_running_loop()
                loop.set_default_executor(ThreadPoolExecutor(max_workers=1))
                loop.run_in_executor(None, held_hash)
                await system_resolver("localhost", 80)
            else:
                await _run_hashing(held_hash)
    finally:
        event("request_joined")


def main() -> None:
    configure_logging("INFO", json=True)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    event("port", port=sock.getsockname()[1])

    class ObservedServer(ShutdownServer):
        async def _serve(self, sockets: list[socket.socket] | None = None) -> None:
            event("loop", implementation=type(asyncio.get_running_loop()).__module__)
            await super()._serve(sockets)
            event("process_finished", accounting_complete=self.process.accounting_complete)

    server = ObservedServer(
        uvicorn.Config(
            application,
            log_level="warning",
            loop="auto" if sys.argv[1] == "hidden_executor" else "asyncio",
        ),
        budget=ShutdownBudget(0.4, 0.05, 0.05, 0.05),
    )
    server.owns_executor = True
    server.run(sockets=[sock])


if __name__ == "__main__":
    main()
