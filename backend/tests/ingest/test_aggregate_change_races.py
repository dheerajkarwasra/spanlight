"""Concurrent first inserts cannot erase the old hours a later correction must invalidate."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text

from app.attribution.refresh import refresh_attribution
from app.db.rls import bind_project
from app.end_users.refresh import refresh_user_stats
from app.ingest.aggregate_changes import lock_trace_identities
from app.ingest.pipeline import IngestTarget, ingest_spans
from app.reconciliation.rollups import refresh_cost_rollups
from app.rollups.compute import compute_rollups
from app.rollups.coverage import lock_aggregate_sources
from app.rollups.dirty import ALL, ack_dirty_hour, complete_dirty_consumers
from tests.attribution.support import SessionFactory
from tests.helpers import create_workspace, new_trace_id, span
from tests.rollups.dirty_support import dirty_count, dirty_row, dirty_rows


async def test_absent_identity_serializes_capture_after_consumed_first_generation(
    browser_factory: Any, session_factory: SessionFactory
) -> None:
    workspace = await create_workspace(await browser_factory("dirty-race@example.com"))
    project = uuid.UUID(workspace.project_id)
    target = IngestTarget(project, True, uuid.UUID(workspace.org_id))
    trace_id = new_trace_id()
    old = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) - timedelta(days=5)
    moved = old + timedelta(hours=3)
    original = span(trace_id=trace_id, start=old)
    correction = span(trace_id=trace_id, span_id=original["span_id"], start=moved)

    async def second_writer() -> None:
        async with session_factory() as db:
            result = await ingest_spans(db, target, [correction])
            assert result.accepted == 1 and not result.rejected
            await db.commit()

    async with session_factory() as first:
        # This transaction also fulfills all repair obligations, so take the source lock first.
        assert await lock_aggregate_sources(first, project)
        assert await lock_trace_identities(first, project, [trace_id])
        first_pid = await first.scalar(text("SELECT pg_backend_pid()"))
        second = asyncio.create_task(second_writer())
        try:
            async with asyncio.timeout(5), session_factory() as observer:
                while not await observer.scalar(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM pg_stat_activity WHERE "
                        "datname=current_database() "
                        "AND :blocker = ANY(pg_blocking_pids(pid)))"
                    ),
                    {"blocker": first_pid},
                ):
                    await observer.execute(text("SELECT pg_stat_clear_snapshot()"))
                    await asyncio.sleep(0.01)
            assert not second.done()
            result = await ingest_spans(first, target, [original])
            assert result.accepted == 1 and not result.rejected
            observed = await dirty_row(first)
            generation, shard = observed.generation, observed.shard
            await compute_rollups(first, project, old, old + timedelta(hours=1))
            await refresh_cost_rollups(first, project, [old.date()])
            await refresh_attribution(first, project, [], [old.date()], backfill_days=())
            await refresh_user_stats(first, project, [old.date()])
            assert await complete_dirty_consumers(first, project, old, generation, ALL, shard=shard)
            assert await ack_dirty_hour(first, project, old, generation, shard=shard)
            await first.commit()
            await asyncio.wait_for(second, 5)
        finally:
            if not second.done():
                second.cancel()
            await asyncio.gather(second, return_exceptions=True)
    async with session_factory() as db:
        await bind_project(db, project)
        rows = await dirty_rows(db)
        assert [row.bucket_hour for row in rows] == [old, moved]
        assert rows[0].generation != generation
        assert rows[0].pending_consumers == rows[1].pending_consumers == ALL
        await compute_rollups(db, project, old, old + timedelta(hours=1))
        assert (
            await db.scalar(
                text("SELECT count(*) FROM span_rollups_hourly WHERE bucket_start=:hour"),
                {"hour": old},
            )
            == 0
        )
        assert await dirty_count(db) == 2
