"""`expire_exports` finds the due exports of every project with the bypass. It must expire exactly
those, delete only their files, and leave every other export and object as it was.
"""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

from app.exports.expiry import expire_exports
from app.exports.service import export_prefix
from app.storage.object_store import ObjectInfo
from tests.jobs.bypass_support import SessionFactory, execute, fetch, new_projects, seed_row

_EXPORTS = "SELECT id, project_id, status, storage_key, error_code FROM exports"


class MemoryStore:
    """An object store in a dict; `fail_on` names keys whose delete raises."""

    def __init__(self, fail_on: frozenset[str] = frozenset()) -> None:
        self.objects: dict[str, bytes] = {}
        self.fail_on = fail_on

    async def put(self, key: str, body: bytes | AsyncIterator[bytes], content_type: str) -> None:
        assert isinstance(body, bytes)
        self.objects[key] = body

    async def delete(self, key: str) -> None:
        if key in self.fail_on:
            raise OSError("storage unavailable")
        self.objects.pop(key, None)

    async def list(self, prefix: str) -> list[ObjectInfo]:
        now = datetime.now(UTC)
        return [
            ObjectInfo(key, len(body), now)
            for key, body in sorted(self.objects.items())
            if key.startswith(prefix)
        ]

    def open(self, key: str) -> AsyncIterator[bytes]:
        raise NotImplementedError

    def presigned_get_url(self, key: str, expires_in: int) -> str:
        raise NotImplementedError


async def _export(
    session_factory: SessionFactory,
    store: MemoryStore,
    project_id: uuid.UUID,
    *,
    status: str,
    expires_at: datetime | None,
) -> dict[str, Any]:
    """An export in `status`; a finished one has its file in `store`."""
    row = await seed_row(session_factory, "exports", project_id)
    export_id = uuid.UUID(str(row["id"]))
    key = f"{export_prefix(project_id, export_id)}/1.jsonl"
    done = status == "done"
    await execute(
        session_factory,
        "UPDATE exports SET status = CAST(:status AS export_status), storage_key = :key, "
        "size_bytes = :size, row_count = :size, completed_at = :completed, expires_at = :expires "
        "WHERE id = :id",
        {
            "id": export_id,
            "status": status,
            "key": key if done else None,
            "size": 1 if done else None,
            "completed": datetime.now(UTC) if done else None,
            "expires": expires_at,
        },
    )
    if done:
        await store.put(key, b"{}", "application/x-ndjson")
    return {"id": export_id, "project_id": project_id, "key": key}


async def test_only_due_exports_of_every_project_expire_and_only_their_files_go(
    session_factory: SessionFactory,
) -> None:
    now = datetime.now(UTC)
    a, b = await new_projects(session_factory, 2)
    store = MemoryStore()
    due, kept = [], []
    for project in (a, b):
        due.append(
            await _export(
                session_factory, store, project, status="done", expires_at=now - timedelta(hours=1)
            )
        )
        kept.append(
            await _export(
                session_factory, store, project, status="done", expires_at=now + timedelta(hours=1)
            )
        )
        kept.append(
            await _export(session_factory, store, project, status="queued", expires_at=None)
        )
    before = {row["id"]: row for row in await fetch(session_factory, _EXPORTS)}

    expired = await expire_exports(session_factory, store, now=now)

    assert expired == 2
    after = {row["id"]: row for row in await fetch(session_factory, _EXPORTS)}
    for export in due:
        row = after[export["id"]]
        assert (row["project_id"], row["status"], row["storage_key"]) == (
            export["project_id"],
            "expired",
            None,
        )
        assert export["key"] not in store.objects
    for export in kept:
        assert after[export["id"]] == before[export["id"]]
    assert set(store.objects) == {
        export["key"] for export in kept if before[export["id"]]["storage_key"]
    }


async def test_a_storage_failure_for_one_project_does_not_stop_the_others(
    session_factory: SessionFactory,
) -> None:
    now = datetime.now(UTC)
    a, b = await new_projects(session_factory, 2)
    store = MemoryStore()
    stuck = await _export(
        session_factory, store, a, status="done", expires_at=now - timedelta(hours=2)
    )
    store.fail_on = frozenset({stuck["key"]})
    fine = await _export(
        session_factory, store, b, status="done", expires_at=now - timedelta(hours=1)
    )

    assert await expire_exports(session_factory, store, now=now) == 1

    after = {row["id"]: row for row in await fetch(session_factory, _EXPORTS)}
    assert after[stuck["id"]]["status"] == "done"  # the next run tries again
    assert after[stuck["id"]]["storage_key"] == stuck["key"] and stuck["key"] in store.objects
    assert (after[fine["id"]]["status"], after[fine["id"]]["storage_key"]) == ("expired", None)


async def test_without_object_storage_no_export_is_marked_expired(
    session_factory: SessionFactory,
) -> None:
    now = datetime.now(UTC)
    (a,) = await new_projects(session_factory, 1)
    export = await _export(
        session_factory, MemoryStore(), a, status="done", expires_at=now - timedelta(hours=1)
    )

    assert await expire_exports(session_factory, None, now=now) == 0

    rows = await fetch(session_factory, _EXPORTS)
    assert [(row["id"], row["status"]) for row in rows] == [(export["id"], "done")]
