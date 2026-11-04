"""Pinned Uvicorn 0.54.0 adapter: own startup and join requests before lifespan shutdown."""

import asyncio
import os
import signal
import socket
from types import FrameType

import structlog
import uvicorn
from uvicorn.lifespan.on import LifespanOn

from app.config import get_settings
from app.gateway.cleanup_join import join
from app.gateway.stream_forward import pending_finalizations
from app.process_shutdown import (
    ProcessShutdown,
    ShutdownBudget,
    budget_from_settings,
    current,
    wait_owned,
)

logger = structlog.get_logger(__name__)


class OwnedLifespan(LifespanOn):
    owner: asyncio.Task[None] | None = None
    entered = False

    async def main(self) -> None:
        self.owner = asyncio.current_task()
        await super().main()

    async def startup(self) -> None:
        # Server.startup still owns upstream socket binding after our interruptible startup.
        if not self.entered:
            self.entered = True
            await super().startup()

    async def shutdown(self) -> None:
        await super().shutdown()
        process = current.get()
        if process is not None and (self.shutdown_failed or self.error_occurred):
            process.incomplete(phase="resources", reason="close_failed")


class ShutdownServer(uvicorn.Server):
    def __init__(
        self, config: uvicorn.Config, *, budget: ShutdownBudget, owns_executor: bool = False
    ) -> None:
        super().__init__(config)
        self.process = ProcessShutdown(budget)
        self.owns_executor = owns_executor
        self.shutdown_task: asyncio.Task[None] | None = None

    async def _serve(self, sockets: list[socket.socket] | None = None) -> None:
        if uvicorn.__version__ != "0.54.0":
            raise RuntimeError("Revalidate the shutdown adapter before upgrading Uvicorn")
        token = current.set(self.process)
        watcher = asyncio.create_task(self.process.watch())
        try:
            # Called within upstream capture_signals, before importing the application.
            if not self.config.loaded:
                self.config.load()
            self.config.lifespan_class = OwnedLifespan
            try:
                await super()._serve(sockets)
            except asyncio.CancelledError:
                if self.started:
                    await self.shutdown(sockets)
                raise
        finally:
            try:
                if self.owns_executor:
                    await wait_owned(asyncio.create_task(self.process.join_default_executor()))
                    # Uvicorn's "Application shutdown complete." covers the ASGI lifespan only.
                    logger.info(
                        "process.shutdown_finished",
                        accounting_complete=self.process.accounting_complete,
                    )
            finally:
                watcher.cancel()
                await asyncio.gather(watcher, return_exceptions=True)
                current.reset(token)

    def handle_exit(self, sig: int, frame: FrameType | None) -> None:
        self.process.begin()
        super().handle_exit(sig, frame)
        # signal.signal alone does not wake a selector blocked in application startup.
        asyncio.get_running_loop().call_soon_threadsafe(self.process.stop.set)
        if self.force_exit:
            self.process.incomplete()
            # Forced SIGINT must not unwind pools under unjoined database users.
            signal.signal(signal.SIGINT, signal.SIG_DFL)
            signal.raise_signal(signal.SIGINT)

    async def startup(self, sockets: list[socket.socket] | None = None) -> None:
        startup = asyncio.create_task(self.lifespan.startup())
        stopped = asyncio.create_task(self.process.stop.wait())
        try:
            await asyncio.wait({startup, stopped}, return_when=asyncio.FIRST_COMPLETED)
            if self.should_exit:
                owner = self.lifespan.owner
                if owner is not None and not owner.done():
                    owner.cancel()
                    await self.process.join_producers(lambda: {owner})
                await wait_owned(startup)
                return
            await startup
            await super().startup(sockets)
        except asyncio.CancelledError:
            self.process.begin()
            owner = self.lifespan.owner
            if owner is not None and not owner.done():
                if not owner.cancelling():
                    owner.cancel()
                await wait_owned(asyncio.create_task(self.process.join_producers(lambda: {owner})))
            await wait_owned(startup)
            raise
        finally:
            stopped.cancel()
            await asyncio.gather(stopped, return_exceptions=True)

    async def shutdown(self, sockets: list[socket.socket] | None = None) -> None:
        if self.shutdown_task is None:
            self.shutdown_task = asyncio.create_task(self._shutdown(sockets))
        await wait_owned(self.shutdown_task)

    async def _shutdown(self, sockets: list[socket.socket] | None) -> None:
        self.process.begin()
        # These are the upstream shutdown entry operations, before its unsafe cancellation seam.
        for server in self.servers:
            server.close()
        for sock in sockets or []:
            sock.close()
        for connection in list(self.server_state.connections):
            connection.shutdown()
        requests = set(self.server_state.tasks)

        def pending() -> set[asyncio.Task[None]]:
            requests.update(self.server_state.tasks)
            return requests

        await join(
            pending, cancel_at=self.process.work_deadline, deadline=self.process.producer_deadline
        )
        await self.process.join_producers(lambda: pending() | set(pending_finalizations()))
        for server in self.servers:
            await server.wait_closed()
        await self.lifespan.shutdown()
        if self.owns_executor:
            await self.process.join_default_executor()


def run_application(*, gateway: bool = False, host: str | None = None) -> None:
    """Shared production launcher; retain Uvicorn's loop and signal capture semantics."""
    os.environ.pop("MIGRATION_DATABASE_URL", None)
    settings = get_settings()
    config = uvicorn.Config(
        "app.main:create_gateway_app" if gateway else "app.main:app",
        factory=gateway,
        host=host or ((os.environ.get("GATEWAY_HOST") or "0.0.0.0") if gateway else "0.0.0.0"),  # noqa: S104
        port=int(os.environ.get("PORT") or (8001 if gateway else 8000)),
        proxy_headers=True,
        forwarded_allow_ips="*",
    )
    server = ShutdownServer(config, budget=budget_from_settings(settings), owns_executor=True)
    server.run()
    if not server.started and not server.should_exit:
        raise SystemExit(3)


if __name__ == "__main__":
    run_application()
