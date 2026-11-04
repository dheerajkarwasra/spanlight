"""Short worker database operations, including commit and session cleanup."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.observability import WORKER_OPERATION_FAILURES
from app.db.timeouts import set_statement_limit
from app.process_shutdown import current

Operation = Literal["schedule", "claim", "complete", "fail", "retry"]
# Quiet PostgreSQL 17 lock/cancellation measurements: .local/task-7c2/calibration.log.
OPERATION_SECONDS: dict[Operation, float] = {
    "schedule": 1.0,
    "claim": 0.5,
    "complete": 1.0,
    "fail": 1.0,
    "retry": 1.0,
}
LOCK_TIMEOUT_MS = 100
STATEMENT_TIMEOUT_MS = 250
_SET_LOCK_TIMEOUT = text("SELECT set_config('lock_timeout', :milliseconds, true)")
logger = structlog.get_logger(__name__)


@asynccontextmanager
async def worker_operation(
    factory: async_sessionmaker[AsyncSession], operation: Operation
) -> AsyncIterator[AsyncSession]:
    """A failed/uncertain operation never returns a committed result to its caller."""
    process = current.get()
    deadline = asyncio.get_running_loop().time() + OPERATION_SECONDS[operation]
    if process is not None and process.started is not None:
        deadline = min(deadline, process.join_deadline)
    timeout = asyncio.timeout_at(deadline)
    try:
        async with timeout, factory() as db:
            if process is not None:
                process.operations.add(timeout)
            try:
                await set_statement_limit(db, STATEMENT_TIMEOUT_MS)
                await db.execute(_SET_LOCK_TIMEOUT, {"milliseconds": str(LOCK_TIMEOUT_MS)})
                yield db
            except BaseException:
                # Discard uncertain transactions without waiting for a network rollback ACK.
                await db.invalidate()
                raise
    except (Exception, asyncio.CancelledError) as exc:
        WORKER_OPERATION_FAILURES.labels(operation).inc()
        terminal = operation in {"complete", "fail", "retry"}
        if process is not None and terminal:
            process.accounting_complete = False
        log = logger.bind(persistence_uncertain=True) if terminal else logger
        log.warning(
            "worker_operation_failed",
            operation=operation,
            error_type=type(exc).__name__,
        )
        raise
    finally:
        if process is not None:
            process.operations.discard(timeout)
