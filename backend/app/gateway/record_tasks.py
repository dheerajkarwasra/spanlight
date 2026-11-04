"""Owned recorder task state and cancellation join evidence."""

import asyncio
from dataclasses import dataclass, field

import structlog

from app.gateway.cleanup_join import join

logger = structlog.get_logger(__name__)


@dataclass
class _Writers:
    """The background writes of one event loop, and how many may run and wait."""

    loop: asyncio.AbstractEventLoop
    slots: asyncio.Semaphore
    backlog: int
    backlog_bytes: int
    pending: set["asyncio.Task[None]"] = field(default_factory=set)
    accounting_complete: bool = True
    held_bytes: int = 0  # the stored payload bytes of the pending spans
    dropped: int = 0  # since the last overflow log line
    logged_at: float | None = None


@dataclass(frozen=True)
class RecorderShutdown:
    joined: bool
    accounting_complete: bool


async def shutdown_writers(
    writers: _Writers | None, *, cancel_at: float | None, deadline: float | None = None
) -> RecorderShutdown:
    """Join recorder tasks after external producers stop; deadlines use loop.time()."""
    if writers is None or writers.loop is not asyncio.get_running_loop():
        return RecorderShutdown(joined=True, accounting_complete=True)
    joined = await join(lambda: set(writers.pending), cancel_at=cancel_at, deadline=deadline)
    if not joined:
        logger.warning(
            "process.shutdown_incomplete",
            phase="finalizers",
            reason="join_timeout",
            accounting_complete=False,
        )
    return RecorderShutdown(
        joined=joined, accounting_complete=joined and writers.accounting_complete
    )
