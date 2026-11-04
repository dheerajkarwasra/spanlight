"""Accepted source changes invalidate every old and new trace hour atomically."""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from app.db.rls import bind_project
from app.ingest.aggregate_changes import delete_corrective_traces
from app.ingest.pipeline import IngestTarget, ingest_spans
from app.ingest.store import write_spans
from app.rollups.usage_rollup_backfill import backfill_usage_rollups
from tests.attribution.support import SessionFactory, SpanSeed, TraceSeed, seed_traces
from tests.helpers import bearer, create_workspace, new_trace_id, span
from tests.rollups.dirty_support import dirty_count, dirty_row, dirty_rows


async def test_late_replay_marks_old_new_and_untouched_trace_hours(
    browser_factory: Any,
    client: httpx.AsyncClient,
    session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.rollups.dirty.random.randrange", lambda _: 5)
    workspace = await create_workspace(await browser_factory("dirty-capture@example.com"))
    project = uuid.UUID(workspace.project_id)
    old = datetime.now(UTC).replace(hour=23, minute=0, second=0, microsecond=0) - timedelta(days=5)
    moved, sibling = old + timedelta(hours=2), old + timedelta(hours=4)
    trace_id = new_trace_id()
    first = span(trace_id=trace_id, start=old)
    second = span(trace_id=trace_id, start=sibling)
    result = await client.post(
        "/v1/traces", json={"spans": [first, second]}, headers=bearer(workspace.api_key)
    )
    assert result.status_code == 200 and result.json()["rejected"] == [], result.text
    changed = span(
        trace_id=trace_id,
        span_id=first["span_id"],
        start=moved,
        model="gpt-4o",
        trace={
            "name": "changed",
            "environment": "stage",
            "release": "v2",
            "external_user_id": "alice",
        },
    )
    result = await client.post(
        "/v1/traces", json={"spans": [changed]}, headers=bearer(workspace.api_key)
    )
    assert result.status_code == 200 and result.json()["rejected"] == [], result.text
    async with session_factory() as db:
        await bind_project(db, project)
        rows = [(row.bucket_hour, row.pending_consumers) for row in await dirty_rows(db)]
    assert rows == [(old, 15), (moved, 15), (sibling, 15)]


async def test_existing_unmarked_trace_captures_departed_hour_and_gateway_owned_noop(
    browser_factory: Any,
    client: httpx.AsyncClient,
    session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.rollups.dirty.random.randrange", lambda _: 5)
    workspace = await create_workspace(await browser_factory("dirty-existing@example.com"))
    project = uuid.UUID(workspace.project_id)
    old = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) - timedelta(days=5)
    moved = old + timedelta(days=1)
    async with session_factory() as db:
        await bind_project(db, project)
        ids = await seed_traces(
            db, project, {"legacy": TraceSeed(old, (SpanSeed("llm", old, Decimal(1), 10, 2),))}
        )
        span_id = await db.scalar(text("SELECT span_id FROM spans"))
        await db.commit()
    body = span(trace_id=ids["legacy"], span_id=span_id, start=moved)
    response = await client.post(
        "/v1/traces", json={"spans": [body]}, headers=bearer(workspace.api_key)
    )
    assert response.status_code == 200 and response.json()["rejected"] == []
    async with session_factory() as db:
        await bind_project(db, project)
        assert [row.bucket_hour for row in await dirty_rows(db)] == [old, moved]
        generation = (await dirty_row(db, hour=moved)).generation
        await db.execute(text("UPDATE spans SET source_key_id=:key"), {"key": uuid.uuid4()})
        await db.commit()
    changed = span(
        trace_id=ids["legacy"],
        span_id=span_id,
        start=moved + timedelta(hours=3),
        trace={"environment": "stage"},
    )
    response = await client.post(
        "/v1/traces", json={"spans": [changed]}, headers=bearer(workspace.api_key)
    )
    assert response.status_code == 200 and response.json()["rejected"] == []
    async with session_factory() as db:
        await bind_project(db, project)
        assert await db.scalar(text("SELECT started_at FROM spans")) == moved
        assert await db.scalar(text("SELECT environment FROM traces")) == "stage"
        assert (await dirty_row(db, hour=moved)).generation != generation


async def test_usage_backfill_marks_ancestor_hours_and_corrective_delete_keeps_coverage(
    browser_factory: Any, session_factory: SessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.rollups.dirty.random.randrange", lambda _: 5)
    workspace = await create_workspace(await browser_factory("dirty-backfill@example.com"))
    project = uuid.UUID(workspace.project_id)
    hour = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) - timedelta(days=5)
    child = hour + timedelta(days=1)
    async with session_factory() as db:
        await bind_project(db, project)
        ids = await seed_traces(
            db,
            project,
            {
                "legacy": TraceSeed(
                    hour,
                    (
                        SpanSeed("llm", hour, Decimal(1), 10, 2),
                        SpanSeed("llm", child, Decimal(2), 20, 4),
                    ),
                )
            },
        )
        parent_id = await db.scalar(
            text("SELECT span_id FROM spans WHERE started_at = :hour"), {"hour": hour}
        )
        # Legacy imported rows have not yet had usage ancestry recomputed.
        await db.execute(
            text("UPDATE spans SET parent_span_id=:parent WHERE started_at=:child"),
            {"parent": parent_id, "child": child},
        )
        await db.commit()
    assert await backfill_usage_rollups(session_factory, since=hour) == 1
    assert await backfill_usage_rollups(session_factory, since=hour) == 1
    async with session_factory() as db:
        await bind_project(db, project)
        assert (
            await db.scalar(
                text("SELECT usage_rollup FROM spans WHERE span_id=:span"), {"span": parent_id}
            )
            is True
        )
        assert await db.scalar(text("SELECT cost_usd FROM traces")) == Decimal(2)
        assert [row.bucket_hour for row in await dirty_rows(db)] == [hour, child]
        assert await delete_corrective_traces(db, project, [ids["legacy"]]) == 1
        assert await db.scalar(text("SELECT count(*) FROM aggregate_incomplete_hours")) == 0
        assert await db.scalar(text("SELECT count(*) FROM traces")) == 0
        assert await dirty_count(db) == 2
        await db.rollback()
        await bind_project(db, project)
        assert await db.scalar(text("SELECT count(*) FROM traces")) == 1


async def test_ingest_rollback_leaves_no_sources_or_markers_and_empty_batch_is_safe(
    browser_factory: Any, session_factory: SessionFactory
) -> None:
    workspace = await create_workspace(await browser_factory("dirty-ingest-rollback@example.com"))
    project = uuid.UUID(workspace.project_id)
    target = IngestTarget(project, True, uuid.UUID(workspace.org_id))
    async with session_factory() as db:
        outcome = await ingest_spans(db, target, [span(trace_id=new_trace_id())])
        assert outcome.accepted == 1 and not outcome.rejected
        assert await dirty_count(db) == 1
        await db.rollback()
        await bind_project(db, project)
        assert await db.scalar(text("SELECT count(*) FROM traces")) == 0
        assert await dirty_count(db) == 0
        await write_spans(db, project, [], None)
        assert await dirty_count(db) == 0
