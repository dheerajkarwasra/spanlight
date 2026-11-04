"""Bounded operator recovery of the exact admitted recent scheduling prefix."""

import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.aggregate_job_progress import AggregateJobProgress
from app.db.rls import bind_project
from app.db.session import create_engine, create_session_factory
from app.jobs import aggregate_progress as progress
from app.rollups.compute import floor_hour
from app.rollups.recent_queries import KIND, closed_target, settle_recent


class RecentRecoveryError(RuntimeError):
    """An expected stop the CLI reports as an error line: no admission, or the project is gone."""


@dataclass(frozen=True)
class RecoveryResult:
    cursor: datetime
    end: datetime
    chunks: int
    rows: int
    status: str


async def _cursor(db: AsyncSession, project: uuid.UUID, seconds: float) -> datetime:
    async with asyncio.timeout(seconds):
        try:
            await progress.set_limits(db, seconds)
            await bind_project(db, project)
            cursor = await db.scalar(
                select(AggregateJobProgress.next_closed_hour).where(
                    AggregateJobProgress.project_id == project,
                    AggregateJobProgress.kind == KIND,
                )
            )
            if cursor is None:
                raise RecentRecoveryError("Required recent aggregate admission is missing")
            await db.rollback()
            return cursor
        except BaseException:
            await db.invalidate()
            raise


async def resume_recent(
    db: AsyncSession,
    project: uuid.UUID,
    start: datetime,
    end: datetime,
    *,
    started: float | None = None,
) -> RecoveryResult:
    started = asyncio.get_running_loop().time() if started is None else started
    now = datetime.now(UTC)
    if start.utcoffset() != timedelta(0) or end.utcoffset() != timedelta(0):
        raise ValueError("--resume-recent endpoints must use UTC")
    if start != floor_hour(start) or end != floor_hour(end):
        raise ValueError("--resume-recent endpoints must be whole UTC hours")
    if not start < end or end - start > timedelta(days=90):
        raise ValueError("--resume-recent requires a positive range of at most 90 days")
    if end > closed_target(now):
        raise ValueError("--to exceeds the closed-hour finalization target")
    deadline = started + progress.OUTER_SECONDS
    chunks = rows = 0
    async with asyncio.timeout_at(deadline):
        cursor = await _cursor(
            db, project, min(progress.DISCOVERY_SECONDS, progress.remaining(deadline))
        )
        if start > cursor:
            raise ValueError(f"--from must equal the durable cursor {cursor.isoformat()}")
        if start < cursor:
            return RecoveryResult(cursor, end, 0, 0, "stale; resume at current cursor")
        while cursor < end and chunks < progress.MAX_CANDIDATES:
            if not progress.remaining(started + progress.START_SECONDS):
                break
            seconds = min(progress.TRANSACTION_SECONDS, progress.remaining(deadline))
            try:
                async with asyncio.timeout(seconds):
                    try:
                        await progress.set_limits(db, seconds)
                        result = await settle_recent(
                            db, project, now, recent=False, expected=cursor, until=end
                        )
                        if result is None:
                            raise RecentRecoveryError("Project was deleted during recent recovery")
                        await db.commit()
                    except BaseException:
                        await db.invalidate()
                        raise
            except (DBAPIError, TimeoutError):
                cursor = await _cursor(
                    db, project, min(progress.DISCOVERY_SECONDS, progress.remaining(deadline))
                )
                return RecoveryResult(cursor, end, chunks, rows, "incomplete")
            cursor = result.cursor
            if result.stale:
                return RecoveryResult(cursor, end, chunks, rows, "stale; resume at current cursor")
            chunks += 1
            rows += result.rows
        return RecoveryResult(
            cursor, end, chunks, rows, "complete" if cursor >= end else "incomplete"
        )


async def recover_recent(
    database_url: str,
    project: uuid.UUID,
    start: datetime,
    end: datetime,
) -> RecoveryResult:
    """Own the CLI connection lifecycle inside the same original cooperative deadline."""
    started = asyncio.get_running_loop().time()
    deadline = started + progress.OUTER_SECONDS
    engine = create_engine(database_url, pool_size=1)
    try:
        async with asyncio.timeout_at(deadline), create_session_factory(engine)() as db:
            return await resume_recent(db, project, start, end, started=started)
    finally:
        try:
            async with asyncio.timeout(progress.remaining(deadline)):
                await engine.dispose()
        except TimeoutError:
            # Active transactions already invalidate on cancellation; discard the idle pool.
            await engine.dispose(close=False)
            structlog.get_logger(__name__).warning("recent_recovery_cleanup_timeout")
