"""Actual app/worker lifecycles with test-only gates and monotonic phase observations."""

import asyncio
import json
import os
import socket
import sys
import uuid
from types import FrameType
from typing import Any

import httpx
import uvicorn
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app import main
from app.alerts.channels import ChannelClients
from app.config import Settings
from app.gateway import recorder, span_losses, stream_forward
from app.jobs import queue, worker_lifecycle
from app.jobs.context import TaskContext
from app.jobs.metrics_server import serve_metrics
from app.jobs.worker import Worker
from app.process_shutdown import ProcessShutdown, ShutdownBudget
from app.server import ShutdownServer
from app.telemetry_losses import flush
from tests.gateway_fixtures import CREDENTIALS_KEYS
from tests.process_shutdown.server_child import event


def observe(module: Any, name: str, label: str) -> None:
    original = getattr(module, name)

    async def measured(*args: Any, **kwargs: Any) -> Any:
        event(label + "_start")
        try:
            return await original(*args, **kwargs)
        finally:
            event(label + "_end")

    setattr(module, name, measured)


def configure(data: dict[str, Any]) -> Settings:
    original_join = worker_lifecycle.join

    async def observed_join(*args: Any, **kwargs: Any) -> Any:
        event("work_join_start", now=asyncio.get_running_loop().time(), **kwargs)
        try:
            return await original_join(*args, **kwargs)
        finally:
            event("work_join_end")

    worker_lifecycle.join = observed_join
    for module, name, label in (
        (recorder, "shutdown", "recorder"),
        (ProcessShutdown, "join_default_executor", "executor"),
        (flush, "stop", "flush"),
        (httpx.AsyncClient, "aclose", "http"),
        (ChannelClients, "aclose", "channel"),
        (AsyncEngine, "dispose", "pool"),
        (main, "shutdown_tracing", "tracing"),
        (worker_lifecycle, "shutdown_tracing", "tracing"),
    ):
        observe(module, name, label)
    return Settings(
        database_url=data["database_url"] + f"?application_name=task15d_{os.getpid()}",
        credentials_keys=CREDENTIALS_KEYS,
        gateway_allow_insecure_base_urls=True,
        worker_required=False,
        log_json=True,
        log_level="WARNING",
        _env_file=None,
    )


def server(data: dict[str, Any], settings: Settings, budget: ShutdownBudget) -> None:
    case = data["case"]
    finalizer_started = asyncio.Event()
    if case == "startup":

        async def blocked(sessions: Any) -> None:
            try:
                async with sessions() as db:
                    pid = await db.scalar(text("SELECT pg_backend_pid()"))
                    event("startup_blocked", pid=pid)
                    await db.execute(text("SELECT pg_advisory_xact_lock(150040)"))
            finally:
                event("startup_joined")

        main._sync_prices = blocked
    if case == "startup_failure":

        async def failed(sessions: Any) -> None:
            async with sessions() as db:
                event("startup_failed", pid=await db.scalar(text("SELECT pg_backend_pid()")))
            raise RuntimeError("test startup failure")

        main._sync_prices = failed
    if case == "close_failure":
        original_close = httpx.AsyncClient.aclose
        failed_once = False

        async def failed_close(client: httpx.AsyncClient) -> None:
            nonlocal failed_once
            await original_close(client)
            if not failed_once:
                failed_once = True
                raise RuntimeError("test resource close failure")

        httpx.AsyncClient.aclose = failed_close
    application = (main.create_gateway_app if data["role"] == "gateway" else main.create_app)(
        settings
    )

    async def sql() -> dict[str, bool]:
        if case == "loss_flush":
            span_losses.lose(uuid.UUID(data["project_id"]), "error")
            event("loss_queued")
            return {"ok": True}
        try:
            async with application.state.session_factory() as db:
                pid = await db.scalar(text("SELECT pg_backend_pid()"))
                event("sql_blocked", pid=pid)
                await db.execute(text("SELECT pg_advisory_xact_lock(150040)"))
        finally:
            event("request_joined")
        return {"ok": True}

    application.add_api_route("/probe", sql)
    if case.startswith("stream"):
        original = stream_forward.close_quietly

        async def held_close(response: Any) -> None:
            event("finalizer_start")
            finalizer_started.set()
            if case == "stream_stuck":
                await asyncio.Event().wait()
            else:
                await asyncio.sleep(data.get("close_delay", 0.15))
            await original(response)
            event("finalizer_end")

        stream_forward.close_quietly = held_close

    class ObservedServer(ShutdownServer):
        def handle_exit(self, sig: int, frame: FrameType | None) -> None:
            event("signal", signal=sig)
            super().handle_exit(sig, frame)
            event("deadline", deadline=self.process.deadline)

        async def shutdown(self, sockets: list[socket.socket] | None = None) -> None:
            if case == "stream_waiter":
                first = asyncio.create_task(super().shutdown(sockets))
                await finalizer_started.wait()
                first.cancel()
                event("shutdown_waiter_cancelled")
                await super().shutdown(sockets)
                event("second_shutdown_joined")
                try:
                    await first
                except asyncio.CancelledError:
                    event("first_shutdown_joined")
            else:
                await super().shutdown(sockets)
            event("finished", accounting_complete=self.process.accounting_complete)

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    event("port", port=sock.getsockname()[1])
    config = uvicorn.Config(application, lifespan="on", log_level="warning", access_log=False)
    if case == "stream_slow":

        async def observed(scope: Any, receive: Any, send: Any) -> None:
            async def sending(message: Any) -> None:
                timer = asyncio.get_running_loop().call_later(0.08, event, "send_stalled")
                try:
                    await send(message)
                finally:
                    timer.cancel()

            await application(scope, receive, sending)

        config = uvicorn.Config(observed, lifespan="on", log_level="warning", access_log=False)
    if data.get("baseline"):
        uvicorn.Server(config).run(sockets=[sock])
    else:
        ObservedServer(config, budget=budget, owns_executor=True).run(sockets=[sock])


async def worker(data: dict[str, Any], settings: Settings, budget: ShutdownBudget) -> None:
    case = data["case"]

    async def handler(context: TaskContext, payload: dict[str, Any]) -> None:
        event("handler_started")
        try:
            await asyncio.sleep(
                data.get("handler_seconds", 0.1)
                if case in {"short", "terminal", "terminal_ack"}
                else 600
            )
        finally:
            event("handler_joined")

    class TestWorker(Worker):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs["handlers"] = {"shutdown_short": handler, "create_export": handler}
            super().__init__(*args, **kwargs)
            if case == "worker_close_failure":
                event("worker_ready")

    if case == "startup":

        async def prices(db: Any) -> int:
            event("startup_blocked", pid=await db.scalar(text("SELECT pg_backend_pid()")))
            await db.execute(text("SELECT pg_advisory_xact_lock(150040)"))
            return 0

        worker_lifecycle.sync_seed_prices = prices
    if case == "worker_close_failure":
        original_dispose = AsyncEngine.dispose

        async def failing_dispose(self: AsyncEngine, *args: Any, **kwargs: Any) -> None:
            await original_dispose(self, *args, **kwargs)
            raise RuntimeError("test worker close failure")

        AsyncEngine.dispose = failing_dispose
    if case == "worker_tracing_failure":

        def fail_tracing(*args: Any, **kwargs: Any) -> None:
            event("tracing_failed")
            raise RuntimeError("test tracing acquisition failure")

        worker_lifecycle.configure_worker_tracing = fail_tracing
    if case == "worker_price_failure":

        async def fail_prices(db: Any) -> int:
            event("prices_failed")
            raise RuntimeError("test price startup failure")

        worker_lifecycle.sync_seed_prices = fail_prices
    if case == "postclaim":
        original_claim = queue.claim_next

        async def delayed_claim(*args: Any, **kwargs: Any) -> Any:
            job = await original_claim(*args, **kwargs)
            if job is not None:
                event("claimed")
                await asyncio.sleep(0.05)
            return job

        queue.claim_next = delayed_claim
    if case == "terminal_ack":
        original_complete = queue.complete

        async def held_ack(*args: Any, **kwargs: Any) -> Any:
            result = await original_complete(*args, **kwargs)
            event("terminal_committed")
            await asyncio.Event().wait()
            return result

        queue.complete = held_ack
    await worker_lifecycle.run_worker(
        settings, budget, TestWorker, serve_metrics, owns_executor=True
    )


def run(data: dict[str, Any]) -> None:
    settings = configure(data)
    budget = ShutdownBudget(*data.get("budget", [2, 0.2, 0.3, 0.3]))
    if data["role"] == "worker":
        asyncio.run(worker(data, settings, budget))
    else:
        server(data, settings, budget)


if __name__ == "__main__":
    run(json.loads(sys.stdin.readline()))
