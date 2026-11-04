"""The `backup_database` job: dump the database to object storage once a day."""

import asyncio
import contextlib
from datetime import UTC, datetime
from typing import Any

import structlog

from app.backups.service import BackupResult, run_backup
from app.backups.storage import get_backup_object_store
from app.jobs.context import TaskContext
from app.jobs.outcome import JobOutcome
from app.jobs.queue import LeaseLostError
from app.storage.object_store import ObjectStore

logger = structlog.get_logger(__name__)

# A third of the 60 s lease: two missed beats still leave time before another worker may take over.
HEARTBEAT_INTERVAL_SECONDS = 20.0


async def run_backup_database(context: TaskContext, _: dict[str, Any]) -> JobOutcome | None:
    settings = context.settings
    if not settings.backups_enabled:
        logger.info("backup_skipped", reason="BACKUPS_ENABLED is false")
        return JobOutcome.SKIPPED_NOT_CONFIGURED
    if settings.backup_database_url is None:
        logger.info("backup_skipped", reason="BACKUP_DATABASE_URL is not set")
        return JobOutcome.SKIPPED_NOT_CONFIGURED
    store = get_backup_object_store(settings)
    if store is None:
        logger.info("backup_skipped", reason="object storage is not configured")
        return JobOutcome.SKIPPED_NOT_CONFIGURED

    now = datetime.now(UTC)
    started = asyncio.get_running_loop().time()
    result = await _backup_with_heartbeat(context, store, now)
    logger.info(
        "backup_completed",
        key=result.key,
        size_bytes=result.size_bytes,
        seconds=round(asyncio.get_running_loop().time() - started, 1),
    )
    return JobOutcome.OK


async def _backup_with_heartbeat(
    context: TaskContext, store: ObjectStore, now: datetime
) -> BackupResult:
    """Run the backup while extending the job's lease, so a long dump keeps its job.

    The worker allows this kind far more than the usual 50 s, but the lease stays short so a
    crashed worker frees the job quickly. If the lease is lost the dump is cancelled (which kills
    `pg_dump` and discards the partial upload) instead of finishing as an orphan.
    """
    backup = asyncio.create_task(run_backup(context.settings, store, now))
    beat = asyncio.create_task(_heartbeat_until_lost(context))
    try:
        await asyncio.wait({backup, beat}, return_when=asyncio.FIRST_COMPLETED)
        if not backup.done():
            # Only the heartbeat can finish first, and only by raising LeaseLostError.
            await beat
        return backup.result()
    finally:
        for task in (backup, beat):
            task.cancel()
        for task in (backup, beat):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task


async def _heartbeat_until_lost(context: TaskContext) -> None:
    while True:
        await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
        try:
            await context.heartbeat()
        except LeaseLostError:
            raise
        except Exception:
            # A database blip must not kill a healthy dump; the next beat tries again, and the
            # lease check on the next successful one tells us if we lost the job meanwhile.
            logger.warning("backup_heartbeat_failed", exc_info=True)
