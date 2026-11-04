"""One process deadline and permission to release resources used by retained tasks."""

import asyncio
import math
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.gateway import recorder
from app.gateway.cleanup_join import join
from app.gateway.settle_helpers import join_settling, pending_settlements
from app.gateway.stream_forward import pending_finalizations
from app.telemetry_losses import flush

logger = structlog.get_logger(__name__)
current: ContextVar["ProcessShutdown | None"] = ContextVar("process_shutdown", default=None)


@dataclass(frozen=True)
class ShutdownBudget:
    seconds: float
    work_seconds: float
    flush_seconds: float
    close_seconds: float

    def __post_init__(self) -> None:
        values = (self.seconds, self.work_seconds, self.flush_seconds, self.close_seconds)
        if any(not math.isfinite(value) or value <= 0 for value in values):
            raise ValueError("Shutdown allocations must be finite and positive")
        if self.work_seconds + self.flush_seconds + self.close_seconds >= self.seconds:
            raise ValueError("Shutdown must reserve time for producer and transaction joins")


def budget_from_settings(settings: Settings) -> ShutdownBudget:
    return ShutdownBudget(
        settings.shutdown_timeout_seconds,
        settings.shutdown_work_seconds,
        settings.shutdown_flush_seconds,
        settings.shutdown_close_seconds,
    )


class LossFlushOwner:
    """One lifespan's release; the creator retains its pool until every user releases."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions
        self.acquired = False
        self.stopping: asyncio.Task[None] | None = None

    async def stop(self) -> None:
        if not self.acquired:
            return
        if self.stopping is None:
            self.stopping = asyncio.create_task(self._stop())
        await wait_owned(self.stopping)

    async def _stop(self) -> None:
        process = current.get()
        deadline = process.flush_deadline if process is not None else None
        complete = await flush.stop(
            timeout_s=max(0, deadline - asyncio.get_running_loop().time())
            if deadline is not None
            else flush.SHUTDOWN_BUDGET_S,
            deadline=deadline,
        )
        if process is not None:
            process.accounting_complete &= complete
        if flush.pending_tasks(sessions=self.sessions):
            if process is not None:
                process.incomplete()
            await join(
                lambda: set(flush.pending_tasks(sessions=self.sessions)),
                cancel_at=None,
                deadline=None,
            )


@asynccontextmanager
async def shutdown_scope(settings: Settings) -> AsyncIterator[None]:
    """Direct embedding gets the same deadline without acquiring signal ownership."""
    if current.get() is not None:
        yield
        return
    process = ProcessShutdown(budget_from_settings(settings))
    token = current.set(process)
    watcher = asyncio.create_task(process.watch())
    try:
        yield
    finally:
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)
        current.reset(token)


class ProcessShutdown:
    def __init__(self, budget: ShutdownBudget) -> None:
        self.budget = budget
        self.started: float | None = None
        self.stop = asyncio.Event()
        self.accounting_complete = True
        self.producers_joined = False
        self.resources_safe = False
        self.operations: set[asyncio.Timeout] = set()
        self.executor_join: asyncio.Task[None] | None = None

    def begin(self) -> None:
        if self.started is None:
            self.started = asyncio.get_running_loop().time()
            deadline = (
                self.started
                + self.budget.seconds
                - self.budget.flush_seconds
                - self.budget.close_seconds
            )
            for operation in self.operations:
                if operation.expired():
                    continue
                existing = operation.when()
                operation.reschedule(min(existing, deadline) if existing is not None else deadline)
        self.stop.set()

    @property
    def deadline(self) -> float:
        self.begin()
        return (self.started or asyncio.get_running_loop().time()) + self.budget.seconds

    @property
    def work_deadline(self) -> float:
        return self.deadline - self.budget.seconds + self.budget.work_seconds

    @property
    def join_deadline(self) -> float:
        return self.deadline - self.budget.flush_seconds - self.budget.close_seconds

    @property
    def flush_deadline(self) -> float:
        return self.deadline - self.budget.close_seconds

    @property
    def write_deadline(self) -> float:
        return self.work_deadline + (self.join_deadline - self.work_deadline) * 0.75

    @property
    def producer_deadline(self) -> float:
        return (self.work_deadline + self.join_deadline) / 2

    def incomplete(self, *, phase: str = "finalizers", reason: str = "join_timeout") -> None:
        self.accounting_complete = False
        logger.error(
            "process.shutdown_incomplete",
            phase=phase,
            reason=reason,
            accounting_complete=False,
        )

    async def watch(self) -> None:
        await self.stop.wait()
        await asyncio.sleep(max(0, self.deadline - asyncio.get_running_loop().time()))
        self.incomplete(
            phase="resources" if self.resources_safe else "finalizers",
            reason="close_failed" if self.resources_safe else "join_timeout",
        )

    async def join_producers(self, snapshot: Callable[[], set[asyncio.Task[Any]]]) -> None:
        """A timed-out join retains ownership; external hard stop may end the process."""
        observed: set[asyncio.Task[Any]] = set()

        def pending() -> set[asyncio.Task[Any]]:
            observed.update(snapshot())
            return observed

        if not await join(pending, cancel_at=None, deadline=self.producer_deadline):
            self.incomplete()
            # No return into AsyncExitStack or asyncio.run while dependent users are live.
            await join(pending, cancel_at=None, deadline=None)
        if any(not task.cancelled() and task.exception() is not None for task in observed):
            self.incomplete(reason="cleanup_failed")
        self.producers_joined = True

    async def join_default_executor(self) -> None:
        """Top-level process owners join library resolver work before returning to Runner.

        Never call from an embedded lifespan: the host owns its shared event loop. Do not
        pass the executor API a timeout, which can abandon its own joining thread.
        """
        loop = asyncio.get_running_loop()
        if self.executor_join is None:
            self.executor_join = asyncio.create_task(loop.shutdown_default_executor())
        done, _ = await asyncio.wait(
            {self.executor_join}, timeout=max(0, self.deadline - loop.time())
        )
        if not done:
            self.incomplete(phase="resources", reason="close_failed")
        try:
            await wait_owned(self.executor_join)
        except BaseException:
            self.incomplete(phase="resources", reason="close_failed")
            raise

    async def permit_disposal(self, *, flush_owned: bool = False) -> None:
        """Join stream/DB users before the existing resource stack can start unwinding."""
        await self.join_producers(lambda: set(pending_finalizations()))
        joined = await join_settling(cancel_at=self.write_deadline, deadline=self.join_deadline)
        result = await recorder.shutdown(cancel_at=self.write_deadline, deadline=self.join_deadline)
        self.accounting_complete &= result.accounting_complete
        if not joined or not result.joined:
            self.incomplete()
            await join(
                lambda: set(pending_settlements()) | set(recorder.pending_tasks()),
                cancel_at=None,
                deadline=None,
            )
        if flush_owned:
            # The preserved resource callback flushes after close_runtime and retains its pool.
            self.resources_safe = True
            return
        flushed = await flush.stop(
            timeout_s=max(0, self.flush_deadline - asyncio.get_running_loop().time()),
            deadline=self.flush_deadline,
        )
        self.accounting_complete &= flushed
        if flush.pending_tasks():
            self.incomplete()
            await join(lambda: set(flush.pending_tasks()), cancel_at=None, deadline=None)
        self.resources_safe = True


async def wait_owned[T](task: asyncio.Task[T]) -> T:
    """Waiter cancellation cannot release a resource scope before its owner has joined."""
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    result = task.result()
    if cancelled:
        raise asyncio.CancelledError
    return result


@asynccontextmanager
async def disposal_gate(*, flush_owned: bool = False) -> AsyncIterator[None]:
    """Inside the existing stack: prevent unwinding until dependent users have joined."""
    try:
        yield
    finally:
        process = current.get()
        if process is not None:
            await wait_owned(asyncio.create_task(process.permit_disposal(flush_owned=flush_owned)))
