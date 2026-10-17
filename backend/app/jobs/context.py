"""What a task handler receives."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.jobs.outcome import JobOutcome, RetryAfter
from app.jobs.queue import ClaimedJob, extend_lease


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class TaskContext:
    job: ClaimedJob
    settings: Settings
    session_factory: async_sessionmaker[AsyncSession]
    # Wall time for tasks that decide by the calendar (a monthly cap); tests pin it.
    clock: Callable[[], datetime] = _utcnow

    async def heartbeat(self) -> None:
        """Extend the lease; raises LeaseLostError if another worker took the job over.

        Tasks call this before irreversible side effects (e.g. paid API calls).
        """
        async with self.session_factory() as db:
            await extend_lease(db, self.job)


# A handler returns the outcome it wants recorded; returning nothing means `JobOutcome.OK`, and
# `RetryAfter` asks for another attempt after the delay it carries.
TaskHandler = Callable[[TaskContext, dict[str, Any]], Awaitable[JobOutcome | RetryAfter | None]]
