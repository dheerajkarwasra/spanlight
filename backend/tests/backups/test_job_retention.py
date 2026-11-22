"""Unit test ensuring `run_backup_database` invokes retention pruning cleanly."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

from app.backups.jobs import run_backup_database
from app.backups.retention import BACKUP_PREFIX, backup_key
from app.backups.service import BackupResult
from app.config import Settings
from app.jobs.outcome import JobOutcome
from app.storage.object_store import ObjectInfo


class _FakeStore:
    def __init__(self, objects: list[ObjectInfo], fail_delete: str | None = None) -> None:
        self._objects = objects
        self._fail_delete = fail_delete
        self.deleted: list[str] = []

    async def put(self, key: str, body: bytes | AsyncIterator[bytes], content_type: str) -> None:
        return None

    async def open(self, key: str) -> AsyncIterator[bytes]:
        yield b""

    async def list(self, prefix: str) -> list[ObjectInfo]:
        return [obj for obj in self._objects if obj.key.startswith(prefix)]

    async def delete(self, key: str) -> None:
        if key == self._fail_delete:
            raise RuntimeError("permission denied")
        self.deleted.append(key)

    def presigned_get_url(self, key: str, expires_in: int) -> str:
        return f"https://example.invalid/{key}"


class _FakeContext:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def heartbeat(self) -> None:
        return None


async def test_run_backup_database_prunes_expired_backups() -> None:
    now = datetime.now(UTC)
    expired = [
        ObjectInfo(
            key=backup_key(now - timedelta(days=45 + i)),
            size=1024,
            last_modified=now - timedelta(days=45 + i),
        )
        for i in range(2)
    ]
    recent = ObjectInfo(key=backup_key(now - timedelta(days=1)), size=1024, last_modified=now)
    store = _FakeStore([recent, *expired], fail_delete=expired[0].key)
    settings = Settings(
        _env_file=None,
        backups_enabled=True,
        backup_database_url="postgresql://backup:pw@localhost:5432/spanlight",
    )
    ctx: Any = _FakeContext(settings)

    with (
        patch("app.backups.jobs.get_backup_object_store", return_value=store),
        patch(
            "app.backups.jobs.run_backup",
            new=AsyncMock(
                return_value=BackupResult(key=f"{BACKUP_PREFIX}latest.dump", size_bytes=99)
            ),
        ),
    ):
        outcome = await run_backup_database(ctx, {})

    assert outcome == JobOutcome.OK
    assert store.deleted == [expired[1].key]
