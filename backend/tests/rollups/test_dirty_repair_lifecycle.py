"""Observe real PostgreSQL lock waits to exercise repair cancellation and source races."""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.config import Settings
from app.db.rls import bind_project
from app.rollups.dirty import mark_dirty_hours
from tests.attribution.support import SessionFactory, SpanSeed, TraceSeed, seed_traces
from tests.rls.factories import create_project
from tests.rollups.dirty_support import dirty_count, dirty_row
from tests.rollups.test_dirty_repair import repair


async def waiting(factory: SessionFactory, blocker_pid: int) -> None:
    async with asyncio.timeout(2), factory() as db:
        while not await db.scalar(
            text("""
            SELECT EXISTS (SELECT 1 FROM pg_stat_activity
            WHERE datname=current_database() AND :pid=ANY(pg_blocking_pids(pid)))
        """),
            {"pid": blocker_pid},
        ):
            await db.execute(text("SELECT pg_stat_clear_snapshot()"))
            await asyncio.sleep(0.002)


@pytest.mark.parametrize("cancel", [False, True])
async def test_generation_change_and_cancel_do_not_ack_uncommitted_repair(
    session_factory: SessionFactory,
    settings: Settings,
    cancel: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.rollups.dirty.random.randrange", lambda _: 5)
    hour = datetime(2026, 10, 20, 10, tzinfo=UTC)
    async with session_factory() as db:
        project = await create_project(db)
        await bind_project(db, project)
        await seed_traces(
            db, project, {"old": TraceSeed(hour, (SpanSeed("llm", hour, Decimal(1), 10, 2),))}
        )
        await mark_dirty_hours(db, project, [hour])
        original_generation = (await dirty_row(db)).generation
        await db.commit()
    async with session_factory() as blocker:
        await blocker.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
            {"key": f"cost_rollups:{project}"},
        )
        pid = await blocker.scalar(text("SELECT pg_backend_pid()"))
        task = asyncio.create_task(repair(session_factory, settings))
        try:
            await waiting(session_factory, pid)
            if cancel:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                async with session_factory() as writer:
                    await bind_project(writer, project)
                    await mark_dirty_hours(writer, project, [hour])
                    await writer.commit()
                await blocker.rollback()
                await task
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    async with session_factory() as db:
        await bind_project(db, project)
        row = await dirty_row(db)
        assert row.pending_consumers == 15
        if cancel:
            assert row.generation == original_generation
            assert await db.scalar(text("SELECT count(*) FROM span_rollups_hourly")) == 0
        else:
            assert row.generation != original_generation
    await repair(session_factory, settings)
    async with session_factory() as db:
        await bind_project(db, project)
        assert await dirty_count(db) == 0
        assert await db.scalar(text("SELECT sum(span_count) FROM span_rollups_hourly")) == 1


async def test_duplicate_jobs_converge_without_double_counting(
    session_factory: SessionFactory,
    settings: Settings,
) -> None:
    hour = datetime(2026, 10, 20, 10, tzinfo=UTC)
    async with session_factory() as db:
        project = await create_project(db)
        await bind_project(db, project)
        await seed_traces(
            db, project, {"old": TraceSeed(hour, (SpanSeed("llm", hour, Decimal(1), 10, 2),))}
        )
        await mark_dirty_hours(db, project, [hour])
        await db.commit()
    await asyncio.gather(repair(session_factory, settings), repair(session_factory, settings))
    async with session_factory() as db:
        await bind_project(db, project)
        assert await dirty_count(db) == 0
        assert await db.scalar(text("SELECT sum(cost_usd) FROM cost_rollups_daily")) == Decimal(1)
        assert await db.scalar(text("SELECT sum(span_count) FROM span_rollups_hourly")) == 1


async def test_attempt_limit_and_rounds_preserve_later_work_for_restart(
    session_factory: SessionFactory,
    settings: Settings,
) -> None:
    hour = datetime(2026, 10, 1, tzinfo=UTC)
    async with session_factory() as db:
        first = await create_project(db)
        await bind_project(db, first)
        await mark_dirty_hours(db, first, [hour + timedelta(hours=i) for i in range(101)])
        second = await create_project(db)
        await bind_project(db, second)
        await mark_dirty_hours(db, second, [hour])
        await db.commit()
    await repair(session_factory, settings)
    async with session_factory() as db:
        await bind_project(db, second)
        assert await dirty_count(db) == 0
        await bind_project(db, first)
        assert await dirty_count(db) == 2
    await repair(session_factory, settings)
    async with session_factory() as db:
        await bind_project(db, first)
        assert await dirty_count(db) == 0
