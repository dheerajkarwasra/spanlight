"""The `create_export` job writes only the exporting project's traces.

Two projects hold traces with the same trace ids and the same environment, release, user,
session and tag, so a filter that matches one project's traces matches the other's too. The only
thing that keeps project B out of project A's file is tenancy: the job binds every session to A
(row-level security) and its queries name A's id. Failure mode pinned here: a change that drops
both, or reads with the bypass, puts B's prompts in A's download; a session that forgets to bind
the project exports nothing.

Not tested here: that the job never sets the bypass flag. `tests/rls/test_bypass_callers.py`
already fails when any module outside its allow-list (which does not include
`app.exports.jobs`) sets it, so a runtime check would only repeat that.
"""

import csv
import io
import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.config import Settings
from app.db.models import ExportFormat
from app.exports import jobs as export_jobs
from app.jobs.context import TaskContext
from app.jobs.queue import ClaimedJob, claim_next, enqueue
from app.storage.object_store import ObjectInfo
from tests.jobs.bypass_support import SessionFactory, execute, fetch, new_projects, seed_row

SHARED_TRACE_IDS = [f"{index:032x}" for index in range(1, 4)]
ONLY_B_TRACE_ID = "b" * 32


class StreamingMemoryStore:
    """An object store in a dict that accepts the streaming body the export job uploads."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def put(self, key: str, body: bytes | AsyncIterator[bytes], content_type: str) -> None:
        if isinstance(body, bytes):
            self.objects[key] = body
            return
        self.objects[key] = b"".join([chunk async for chunk in body])

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)

    async def list(self, prefix: str) -> list[ObjectInfo]:
        now = datetime.now(UTC)
        return [ObjectInfo(key, len(body), now) for key, body in self.objects.items()]

    def open(self, key: str) -> AsyncIterator[bytes]:
        raise NotImplementedError

    def presigned_get_url(self, key: str, expires_in: int) -> str:
        raise NotImplementedError


async def _seed_traces(
    session_factory: SessionFactory, project_id: uuid.UUID, tenant: str, trace_ids: list[str]
) -> None:
    """Traces whose every filterable field is the same in both projects; only `tenant` differs."""
    started = datetime.now(UTC) - timedelta(hours=1)
    for offset, trace_id in enumerate(trace_ids):
        at = started + timedelta(seconds=offset)
        await execute(
            session_factory,
            "INSERT INTO traces (project_id, trace_id, name, environment, release, "
            "external_user_id, session_id, tags, started_at, ended_at, span_count) "
            "VALUES (:project, :trace, :name, 'prod', 'v1', 'user-1', 'session-1', "
            "ARRAY['shared'], :at, :at + interval '1 second', 1)",
            {"project": project_id, "trace": trace_id, "name": f"trace-{tenant}", "at": at},
        )
        await execute(
            session_factory,
            "INSERT INTO spans (project_id, trace_id, span_id, kind, name, status, started_at, "
            "ended_at, model, input) VALUES (:project, :trace, :span, 'llm', 'call', 'ok', :at, "
            ":at + interval '1 second', 'model-a', CAST(:input AS jsonb))",
            {
                "project": project_id,
                "trace": trace_id,
                "span": trace_id[:16],
                "at": at,
                "input": json.dumps({"prompt": f"secret of tenant {tenant}"}),
            },
        )


async def _queued_export(
    session_factory: SessionFactory, project_id: uuid.UUID, export_format: ExportFormat
) -> uuid.UUID:
    now = datetime.now(UTC)
    row = await seed_row(session_factory, "exports", project_id)
    filters = {
        "from": (now - timedelta(days=1)).isoformat(),
        "to": (now + timedelta(hours=1)).isoformat(),
        "environment": "prod",
        "release": "v1",
        "user_id": "user-1",
        "session_id": "session-1",
        "tag": "shared",
    }
    await execute(
        session_factory,
        "UPDATE exports SET format = CAST(:format AS export_format), "
        "filters = CAST(:filters AS jsonb) WHERE id = :id",
        {"id": row["id"], "format": export_format.value, "filters": json.dumps(filters)},
    )
    return uuid.UUID(str(row["id"]))


async def _run_export(
    session_factory: SessionFactory,
    settings: Settings,
    project_id: uuid.UUID,
    export_id: uuid.UUID,
) -> None:
    payload = {"export_id": str(export_id), "project_id": str(project_id)}
    async with session_factory() as db:
        await enqueue(db, "create_export", payload, max_attempts=1)
        await db.commit()
        job = await claim_next(db)
    assert isinstance(job, ClaimedJob)
    context = TaskContext(job=job, settings=settings, session_factory=session_factory)
    await export_jobs.run_create_export(context, payload)


def _exported_traces(body: bytes, export_format: ExportFormat) -> list[dict[str, Any]]:
    if export_format is ExportFormat.CSV:
        return list(csv.DictReader(io.StringIO(body.decode())))
    return [json.loads(line) for line in body.decode().splitlines()]


@pytest.mark.parametrize("export_format", [ExportFormat.JSONL, ExportFormat.CSV])
async def test_an_export_holds_only_its_own_projects_traces_when_ids_and_filters_collide(
    session_factory: SessionFactory,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    export_format: ExportFormat,
) -> None:
    a, b = await new_projects(session_factory, 2)
    await _seed_traces(session_factory, a, "A", SHARED_TRACE_IDS)
    await _seed_traces(session_factory, b, "B", [*SHARED_TRACE_IDS, ONLY_B_TRACE_ID])
    store = StreamingMemoryStore()
    monkeypatch.setattr(export_jobs, "get_object_store", lambda _settings: store)
    export_id = await _queued_export(session_factory, a, export_format)

    await _run_export(session_factory, settings, a, export_id)

    (row,) = await fetch(
        session_factory,
        "SELECT status::text AS status, storage_key, row_count FROM exports WHERE id = :id",
        {"id": export_id},
    )
    assert (row["status"], row["row_count"]) == ("done", len(SHARED_TRACE_IDS))
    body = store.objects[row["storage_key"]]
    traces = _exported_traces(body, export_format)
    assert sorted(trace["trace_id"] for trace in traces) == SHARED_TRACE_IDS
    assert {trace["name"] for trace in traces} == {"trace-A"}
    assert b"tenant B" not in body and b"trace-B" not in body
    if export_format is ExportFormat.JSONL:
        prompts = {span["input"]["prompt"] for trace in traces for span in trace["spans"]}
        assert prompts == {"secret of tenant A"}
