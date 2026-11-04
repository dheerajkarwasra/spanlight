"""The worker's liveness signal: one `worker_heartbeats` row per process, upserted every 10 s.

`/health/ready` reads the newest `last_seen_at`. The beat runs as its own asyncio task, apart
from the job loop, so a task that runs for hours (a database backup) does not make the worker
look dead, and a stuck job loop that still has a live event loop is caught by the job queue's
leases instead. A failed beat is logged and retried on the next tick; it never stops the worker.
The optional progress callback reports short-loop staleness before each beat, including failed
beats; it does not change the persisted readiness signal.
"""

import asyncio
import contextlib
import os
import socket
import uuid
from collections.abc import Awaitable, Callable
from datetime import timedelta
from importlib.metadata import PackageNotFoundError, version

import structlog
from sqlalchemy import delete, func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import WorkerHeartbeat

logger = structlog.get_logger(__name__)

HEARTBEAT_INTERVAL_SECONDS = 10.0
# A beat that takes longer than this is abandoned (and logged) so one hung connection cannot
# silence the heartbeat for good.
HEARTBEAT_TIMEOUT_SECONDS = 5.0
# Rows of workers that were replaced or stopped. Readiness only looks at the newest row, so this
# only keeps the table to the handful of processes that ran recently.
HEARTBEAT_RETENTION = timedelta(days=1)


def new_worker_id() -> str:
    """An id unique to this start of this process: host, pid and a random suffix."""
    return f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"


def backend_version() -> str:
    try:
        return version("spanlight-backend")
    except PackageNotFoundError:
        return "unknown"


async def beat(
    session_factory: async_sessionmaker[AsyncSession], worker_id: str, worker_version: str
) -> None:
    """Record that `worker_id` is alive now (database clock) and prune long-dead workers."""
    async with session_factory() as db:
        statement = insert(WorkerHeartbeat).values(
            worker_id=worker_id, last_seen_at=func.now(), version=worker_version
        )
        await db.execute(
            statement.on_conflict_do_update(
                index_elements=[WorkerHeartbeat.worker_id],
                set_={"last_seen_at": statement.excluded.last_seen_at, "version": worker_version},
            )
        )
        await db.execute(
            delete(WorkerHeartbeat).where(
                WorkerHeartbeat.last_seen_at < func.now() - HEARTBEAT_RETENTION
            )
        )
        await db.commit()


async def run_heartbeat(
    session_factory: async_sessionmaker[AsyncSession],
    stop: asyncio.Event,
    worker_id: str,
    *,
    interval: float = HEARTBEAT_INTERVAL_SECONDS,
    report_progress: Callable[[], None] | None = None,
    sample_queue: Callable[[], Awaitable[None]] | None = None,
) -> None:
    """Beat immediately, then every `interval` seconds until `stop` is set.

    `sample_queue` runs after each beat attempt; it bounds and handles its own failures.
    """
    worker_version = backend_version()
    while not stop.is_set():
        if report_progress is not None:
            report_progress()
        try:
            async with asyncio.timeout(HEARTBEAT_TIMEOUT_SECONDS):
                await beat(session_factory, worker_id, worker_version)
        except Exception as exc:  # noqa: BLE001 - a missed beat must never take the worker down
            logger.warning("worker_heartbeat_failed", worker_id=worker_id, error=repr(exc))
        if sample_queue is not None:
            await sample_queue()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval)
