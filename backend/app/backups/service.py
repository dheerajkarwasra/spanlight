"""Run `pg_dump` into the object store; `restore_backup` (in `restore.py`) brings it back.

Connection settings reach the child as libpq environment variables, never on its command line
(see `libpq.py`). Error messages name the offending parameter, never its value.
"""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime

import structlog

from app.backups.child import CHUNK_SIZE, ChildProcess
from app.backups.libpq import connection_params, process_environment
from app.backups.restore import RestoreError, restore_backup
from app.backups.retention import backup_key
from app.config import Settings
from app.storage.object_store import ObjectStore

__all__ = [
    "BackupError",
    "BackupNotConfiguredError",
    "BackupResult",
    "RestoreError",
    "connection_params",
    "restore_backup",
    "run_backup",
]

logger = structlog.get_logger(__name__)

PG_DUMP = "pg_dump"
CONTENT_TYPE = "application/octet-stream"


class BackupError(Exception):
    """A backup could not be made. The message is safe to log and to store on the job."""


class BackupNotConfiguredError(BackupError):
    """`BACKUP_DATABASE_URL` is not set."""


@dataclass(frozen=True, slots=True)
class BackupResult:
    key: str
    size_bytes: int


class _DumpStream:
    """`pg_dump`'s standard output as an async iterator that also checks how it exited.

    Raising at end of output, before the store sees the end of its input, is what keeps a failed
    dump out of the bucket: the store aborts the upload instead of completing it.
    """

    def __init__(self, process: ChildProcess) -> None:
        self._process = process
        self.size = 0
        self.complete = False

    async def chunks(self) -> AsyncIterator[bytes]:
        stdout = self._process.process.stdout
        assert stdout is not None  # noqa: S101 - spawned with stdout=PIPE
        while chunk := await stdout.read(CHUNK_SIZE):
            self.size += len(chunk)
            yield chunk
        code, tail = await self._process.finish()
        if code != 0:
            raise BackupError(f"{PG_DUMP} exited with status {code}: {tail}")
        self.complete = True


async def run_backup(settings: Settings, store: ObjectStore, now: datetime) -> BackupResult:
    """Dump the database to `backups/YYYY/MM/DD/spanlight-<UTC timestamp>.dump`.

    The dump is streamed from `pg_dump` to the store, so its size is not bound by memory or
    disk. On any failure the child is killed and nothing is left under the key.
    """
    if settings.backup_database_url is None:
        raise BackupNotConfiguredError("BACKUP_DATABASE_URL is not set")
    try:
        params = connection_params(settings.backup_database_url.get_secret_value())
    except ValueError as error:
        raise BackupError(f"BACKUP_DATABASE_URL is invalid: {error}") from None

    key = backup_key(now)
    try:
        spawned = await asyncio.create_subprocess_exec(
            PG_DUMP,
            "-Fc",
            "--no-unlogged-table-data",
            "--no-password",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=process_environment(params),
        )
    except FileNotFoundError:
        raise BackupError(f"{PG_DUMP} is not installed or not on PATH") from None

    process = ChildProcess(spawned, params.get("password"))
    stream = _DumpStream(process)
    try:
        await store.put(key, stream.chunks(), CONTENT_TYPE)
        if not stream.complete:
            raise BackupError("the upload ended before pg_dump finished")
    except BaseException:
        await process.stop()
        await _discard(store, key)
        raise
    return BackupResult(key=key, size_bytes=stream.size)


async def _discard(store: ObjectStore, key: str) -> None:
    """Best effort: remove whatever a failed backup may have left under `key`."""
    try:
        await store.delete(key)
    except Exception:
        logger.warning("backup.discard_failed", key=key, exc_info=True)
