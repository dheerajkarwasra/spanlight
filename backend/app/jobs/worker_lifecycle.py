"""Worker process ownership begins before price synchronization and ends after DB joins."""

import asyncio
import signal
from collections.abc import Awaitable, Callable, Iterator
from contextlib import AbstractAsyncContextManager, AsyncExitStack, contextmanager
from datetime import UTC, datetime
from types import FrameType
from typing import TYPE_CHECKING

from pydantic import SecretStr

import app.ee.hooks as ee  # noqa: TID251 - worker composition uses only the enterprise hook
from app.alerts.channels import register_channel_deliverers
from app.config import Settings
from app.core.logging import configure_logging
from app.core.sentry import init_sentry
from app.core.tracing import configure_worker_tracing, shutdown_tracing
from app.db.session import create_engine, create_session_factory
from app.gateway.cleanup_join import join
from app.jobs.tasks import TASKS
from app.notifications.defaults import register_default_deliverers
from app.pricing.cost import sync_seed_prices
from app.process_shutdown import (
    LossFlushOwner,
    ProcessShutdown,
    ShutdownBudget,
    current,
    disposal_gate,
    wait_owned,
)
from app.telemetry_losses import flush as loss_flush

if TYPE_CHECKING:
    from app.jobs.worker import Worker


@contextmanager
def capture_signals(process: ProcessShutdown) -> Iterator[None]:
    captured: list[int] = []

    def handle(sig: int, frame: FrameType | None) -> None:
        forced = bool(captured) and sig == signal.SIGINT
        captured.append(sig)
        process.begin()
        if forced:
            process.incomplete()
            signal.signal(signal.SIGINT, signal.SIG_DFL)
            signal.raise_signal(signal.SIGINT)

    loop = asyncio.get_running_loop()
    original = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    for sig in original:
        loop.add_signal_handler(sig, handle, sig, None)
    try:
        yield
    finally:
        for sig, handler in original.items():
            loop.remove_signal_handler(sig)
            signal.signal(sig, handler)
    for captured_signal in reversed(captured):
        signal.raise_signal(captured_signal)


async def run_worker(
    settings: Settings,
    budget: ShutdownBudget,
    worker_factory: type["Worker"],
    metrics: Callable[[int | None, SecretStr | None], AbstractAsyncContextManager[None]],
    *,
    owns_executor: bool = False,
) -> None:
    process = ProcessShutdown(budget)
    token = current.set(process)
    watcher = asyncio.create_task(process.watch())
    try:
        with capture_signals(process):
            owner = asyncio.create_task(_resources(settings, process, worker_factory, metrics))
            stopped = asyncio.create_task(process.stop.wait())
            try:
                await asyncio.wait({owner, stopped}, return_when=asyncio.FIRST_COMPLETED)
                if stopped.done() and not owner.done():
                    # Work task owns both startup and jobs; never cancel its resource scope.
                    await wait_owned(owner)
                else:
                    await owner
            except asyncio.CancelledError:
                process.begin()
                await wait_owned(owner)
                raise
            finally:
                stopped.cancel()
                await asyncio.gather(stopped, return_exceptions=True)
                if owns_executor:
                    await wait_owned(asyncio.create_task(process.join_default_executor()))
    finally:
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)
        current.reset(token)


async def _resources(
    settings: Settings,
    process: ProcessShutdown,
    worker_factory: type["Worker"],
    metrics: Callable[[int | None, SecretStr | None], AbstractAsyncContextManager[None]],
) -> None:
    configure_logging(settings.log_level, json=settings.log_json)
    init_sentry(settings)
    ee.load(settings, datetime.now(UTC))
    register_default_deliverers(settings)
    engine = create_engine(settings.database_url, pool_size=2)
    sessions = create_session_factory(engine)
    async with AsyncExitStack() as cleanup:
        try:
            provider = configure_worker_tracing(settings, engine)
        except BaseException:
            await engine.dispose()
            raise
        cleanup.push_async_callback(shutdown_tracing, provider)
        cleanup.push_async_callback(_close_resource, engine.dispose, process)
        cleanup.push_async_callback(
            _close_resource, register_channel_deliverers(sessions, settings).aclose, process
        )
        flush_owner = LossFlushOwner(sessions)
        loss_flush.start(sessions)
        flush_owner.acquired = True
        cleanup.push_async_callback(flush_owner.stop)
        async with disposal_gate(flush_owned=True):
            worker = worker_factory(sessions, settings, handlers={**TASKS, **ee.tasks()})

            async def work() -> None:
                async with sessions() as db:
                    await sync_seed_prices(db)
                    await db.commit()
                if process.stop.is_set():
                    return
                async with metrics(settings.worker_metrics_port, settings.metrics_token):
                    await worker.run_forever(process.stop)

            task = asyncio.create_task(work())
            stopped = asyncio.create_task(process.stop.wait())
            try:
                await asyncio.wait({task, stopped}, return_when=asyncio.FIRST_COMPLETED)
                if stopped.done():
                    await join(
                        lambda: {task},
                        cancel_at=process.work_deadline,
                        deadline=process.producer_deadline,
                    )
                    await process.join_producers(lambda: {task})
                elif not task.cancelled():
                    task.result()
            finally:
                stopped.cancel()
                await asyncio.gather(stopped, return_exceptions=True)


async def _close_resource(
    callback: Callable[[], Awaitable[None]], process: ProcessShutdown
) -> None:
    try:
        await callback()
    except BaseException:
        process.incomplete(phase="resources", reason="close_failed")
        raise
