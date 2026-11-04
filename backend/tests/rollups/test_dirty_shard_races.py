"""Open writes, mixed writers and deletion around repair never acknowledge committed work."""

import asyncio
import random
import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.rls import bind_project
from app.rollups import dirty, dirty_jobs
from app.rollups.dirty_candidates import Candidate
from tests.attribution.support import SessionFactory, SpanSeed, TraceSeed, seed_traces
from tests.rls.factories import create_project
from tests.rollups.dirty_support import dirty_rows
from tests.rollups.test_dirty_repair import repair
from tests.rollups.test_dirty_repair_lifecycle import waiting

HOUR = datetime(2026, 10, 20, 10, tzinfo=UTC)


async def _counts(db: AsyncSession) -> tuple[int | None, int]:
    row = (
        await db.execute(
            text("""
            SELECT (SELECT sum(span_count) FROM span_rollups_hourly) AS rolled,
                   (SELECT count(*) FROM spans) AS raw
        """)
        )
    ).one()
    return row.rolled, row.raw


@pytest.mark.parametrize("outcome", ["commit", "rollback"])
@pytest.mark.parametrize("writer_shard", [0, 1, -1])
async def test_write_open_while_repair_settles_is_never_acknowledged(
    session_factory: SessionFactory,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    writer_shard: int,
    outcome: str,
) -> None:
    monkeypatch.setattr(dirty, "WRITE_SHARDS", True)
    monkeypatch.setattr(random, "randrange", lambda _: 0)
    async with session_factory() as db:
        project = await create_project(db)
        await bind_project(db, project)
        await seed_traces(
            db, project, {"old": TraceSeed(HOUR, (SpanSeed("llm", HOUR, Decimal(1), 10, 2),))}
        )
        await dirty.mark_dirty_hours(db, project, [HOUR])
        observed = (await dirty_rows(db))[0]
        await db.commit()
    rebuilt, resume = asyncio.Event(), asyncio.Event()
    original = dirty_jobs.refresh_user_stats

    async def pause_after_rebuild(
        db: AsyncSession, project_id: uuid.UUID, days: Sequence[date]
    ) -> int:
        written = await original(db, project_id, days)
        rebuilt.set()
        await resume.wait()
        return written

    monkeypatch.setattr(dirty_jobs, "refresh_user_stats", pause_after_rebuild)
    task = asyncio.create_task(repair(session_factory, settings))
    try:
        await asyncio.wait_for(rebuilt.wait(), 5)
        monkeypatch.setattr(dirty, "WRITE_SHARDS", writer_shard != -1)
        monkeypatch.setattr(random, "randrange", lambda _: writer_shard)
        async with session_factory() as writer:
            await bind_project(writer, project)
            await seed_traces(
                writer,
                project,
                {"new": TraceSeed(HOUR, (SpanSeed("llm", HOUR, Decimal(2), 20, 4),))},
            )
            await dirty.mark_dirty_hours(writer, project, [HOUR])
            pid = await writer.scalar(text("SELECT pg_backend_pid()"))
            resume.set()
            if writer_shard == observed.shard:
                # Settlement of the observed row waits on the writer's open upsert.
                await waiting(session_factory, pid)
                assert not task.done()
            else:
                # Other rows do not block; repair commits without seeing the open row.
                await asyncio.wait_for(task, 5)
            await (writer.commit() if outcome == "commit" else writer.rollback())
        await asyncio.wait_for(task, 5)
    finally:
        resume.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    async with session_factory() as db:
        await bind_project(db, project)
        rows = await dirty_rows(db)
        if outcome == "rollback":
            assert rows == []
            assert await _counts(db) == (1, 1)
            return
        assert [(r.shard, r.pending_consumers) for r in rows] == [(writer_shard, dirty.ALL)]
        assert rows[0].generation != observed.generation
        assert await _counts(db) == (1, 2)
    monkeypatch.setattr(dirty_jobs, "refresh_user_stats", original)
    await repair(session_factory, settings)
    async with session_factory() as db:
        await bind_project(db, project)
        assert await dirty_rows(db) == []
        assert await _counts(db) == (2, 2)


async def test_mixed_old_and_new_writers_do_not_wait_and_settle_together(
    session_factory: SessionFactory, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(random, "randrange", lambda _: 3)
    async with session_factory() as db:
        project = await create_project(db)
        await db.commit()
    async with session_factory() as legacy, session_factory() as sharded:
        monkeypatch.setattr(dirty, "WRITE_SHARDS", False)
        await bind_project(legacy, project)
        await seed_traces(
            legacy, project, {"a": TraceSeed(HOUR, (SpanSeed("llm", HOUR, Decimal(1), 10, 2),))}
        )
        await dirty.mark_dirty_hours(legacy, project, [HOUR])
        monkeypatch.setattr(dirty, "WRITE_SHARDS", True)
        await bind_project(sharded, project)
        await sharded.execute(text("SET LOCAL lock_timeout = '200ms'"))
        await seed_traces(
            sharded, project, {"b": TraceSeed(HOUR, (SpanSeed("llm", HOUR, Decimal(2), 20, 4),))}
        )
        await dirty.mark_dirty_hours(sharded, project, [HOUR])
        await sharded.commit()
        await legacy.commit()
    async with session_factory() as db:
        await bind_project(db, project)
        assert [(r.shard, r.pending_consumers) for r in await dirty_rows(db)] == [
            (-1, dirty.ALL),
            (3, dirty.ALL),
        ]
    await repair(session_factory, settings)
    async with session_factory() as db:
        await bind_project(db, project)
        assert await dirty_rows(db) == []
        assert await _counts(db) == (2, 2)


async def test_project_deletion_removes_both_tables_and_stale_candidate_is_skipped(
    session_factory: SessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with session_factory() as db:
        project = await create_project(db)
        await bind_project(db, project)
        monkeypatch.setattr(dirty, "WRITE_SHARDS", False)
        await dirty.mark_dirty_hours(db, project, [HOUR])
        monkeypatch.setattr(dirty, "WRITE_SHARDS", True)
        await dirty.mark_dirty_hours(db, project, [HOUR])
        assert len(await dirty_rows(db)) == 2
        await db.commit()
    async with session_factory() as db:
        await db.execute(text("DELETE FROM projects WHERE id = :id"), {"id": project})
        await db.commit()
    async with session_factory() as db:
        assert await dirty_jobs._repair_marker(db, Candidate(project, HOUR, HOUR), dirty.ALL) == (
            set()
        )
        await bind_project(db, project)
        assert await dirty_rows(db) == []
