"""Ingest round trips stay constant in the batch's trace count, and dirty hours stay exact."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import event

from app.db.rls import bind_project
from app.ingest.aggregate_changes import trace_hours
from app.ingest.pipeline import IngestTarget, ingest_spans
from tests.attribution.support import SessionFactory
from tests.helpers import create_workspace, new_trace_id, span
from tests.rollups.dirty_support import dirty_rows


async def test_hundred_trace_batch_statement_count(
    browser_factory: Any, session_factory: SessionFactory
) -> None:
    workspace = await create_workspace(await browser_factory("round-trips@example.com"))
    target = IngestTarget(uuid.UUID(workspace.project_id), True, uuid.UUID(workspace.org_id))
    spans = [span(trace_id=trace_id) for trace_id in (new_trace_id() for _ in range(100))]
    spans += [span(trace_id=body["trace_id"]) for body in spans]
    statements: list[str] = []

    def record(_conn: Any, _cursor: Any, statement: str, *_rest: Any) -> None:
        statements.append(statement)

    async with session_factory() as db:
        connection = (await db.connection()).sync_connection
        assert connection is not None
        event.listen(connection, "before_cursor_execute", record)
        try:
            outcome = await ingest_spans(db, target, spans)
        finally:
            event.remove(connection, "before_cursor_execute", record)
        await db.commit()
    assert outcome.accepted == 200 and not outcome.rejected
    executed = len(statements)
    assert executed <= 12, "\n".join(statements)


async def test_dirty_hours_unchanged_by_derivation(
    browser_factory: Any, session_factory: SessionFactory
) -> None:
    workspace = await create_workspace(await browser_factory("round-trip-hours@example.com"))
    project = uuid.UUID(workspace.project_id)
    target = IngestTarget(project, True, uuid.UUID(workspace.org_id))
    old = datetime.now(UTC).replace(hour=23, minute=0, second=0, microsecond=0) - timedelta(days=5)
    moved, sibling = old + timedelta(hours=2), old + timedelta(hours=4)
    trace_id = new_trace_id()
    first = span(trace_id=trace_id, start=old)
    async with session_factory() as db:
        await ingest_spans(db, target, [first, span(trace_id=trace_id, start=sibling)])
        await db.commit()
    changed = span(trace_id=trace_id, span_id=first["span_id"], start=moved, model="gpt-4o")
    async with session_factory() as db:
        await bind_project(db, project)
        # The old two-query result: every hour before the replay plus every hour after it.
        before = await trace_hours(db, project, [trace_id])
        generation = max(row.generation for row in await dirty_rows(db))
        await ingest_spans(db, target, [changed])
        after = await trace_hours(db, project, [trace_id])
        marked = [row.bucket_hour for row in await dirty_rows(db) if row.generation > generation]
        await db.commit()
    assert marked == sorted({*before, *after}) == [old, moved, sibling]
