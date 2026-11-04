"""Repair one hour once, preserving every concurrent generation across both tables."""

import asyncio
import random
import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from prometheus_client import REGISTRY
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.rls import bind_project
from app.rollups import dirty, dirty_jobs
from app.rollups.dirty_candidates import Candidate, Scan, backlog
from tests.attribution.support import SessionFactory, SpanSeed, TraceSeed, seed_traces
from tests.rls.factories import create_project
from tests.rollups.dirty_support import dirty_rows
from tests.rollups.test_dirty_repair import repair


@pytest.mark.parametrize("writer_shard", [1, 0, -1])
async def test_write_after_rebuild_stays_pending_and_next_repair_matches_raw(
    session_factory: SessionFactory,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    writer_shard: int,
) -> None:
    hour = datetime(2026, 10, 20, 10, tzinfo=UTC)
    monkeypatch.setattr(dirty, "WRITE_SHARDS", True, raising=False)
    monkeypatch.setattr(random, "randrange", lambda _: 0)
    async with session_factory() as db:
        project = await create_project(db)
        await bind_project(db, project)
        await seed_traces(
            db, project, {"old": TraceSeed(hour, (SpanSeed("llm", hour, Decimal(1), 10, 2),))}
        )
        await dirty.mark_dirty_hours(db, project, [hour])
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
    deferred_before = (
        REGISTRY.get_sample_value(
            "spanlight_aggregate_repair_outcomes_total", {"outcome": "deferred"}
        )
        or 0
    )
    task = asyncio.create_task(repair(session_factory, settings))
    try:
        await asyncio.wait_for(rebuilt.wait(), 5)
        monkeypatch.setattr(dirty, "WRITE_SHARDS", writer_shard != -1)
        monkeypatch.setattr(random, "randrange", lambda _: writer_shard)
        async with session_factory() as writer:
            await bind_project(writer, project)
            await writer.execute(text("SET LOCAL lock_timeout = '200ms'"))
            await seed_traces(
                writer,
                project,
                {"new": TraceSeed(hour, (SpanSeed("llm", hour, Decimal(2), 20, 4),))},
            )
            await dirty.mark_dirty_hours(writer, project, [hour])
            await writer.commit()
        resume.set()
        await task
    finally:
        resume.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    async with session_factory() as db:
        await bind_project(db, project)
        rows = await dirty_rows(db)
        assert len(rows) == 1
        assert rows[0].shard == writer_shard
        assert rows[0].pending_consumers == dirty.ALL
        assert rows[0].generation != observed.generation
        assert await db.scalar(text("SELECT sum(span_count) FROM span_rollups_hourly")) == 1
    assert (
        REGISTRY.get_sample_value(
            "spanlight_aggregate_repair_outcomes_total", {"outcome": "deferred"}
        )
        == deferred_before + 1
    )
    monkeypatch.setattr(dirty_jobs, "refresh_user_stats", original)
    await repair(session_factory, settings)
    async with session_factory() as db:
        await bind_project(db, project)
        assert await dirty_rows(db) == []
        result = (
            await db.execute(
                text("""
                SELECT (SELECT sum(span_count) FROM span_rollups_hourly) AS rolled_count,
                       (SELECT count(*) FROM spans) AS raw_count,
                       (SELECT sum(cost_usd) FROM span_rollups_hourly) AS rolled_cost,
                       (SELECT sum(cost_usd) FROM spans) AS raw_cost
            """)
            )
        ).one()
        assert result.rolled_count == result.raw_count == 2
        assert result.rolled_cost == result.raw_cost == Decimal(3)


@pytest.mark.parametrize("source_lost", [False, True])
async def test_mixed_masks_settle_each_row_and_rebuild_once(
    session_factory: SessionFactory,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    source_lost: bool,
) -> None:
    rebuilds: list[tuple[datetime, datetime]] = []
    compute = dirty_jobs.compute_rollups

    async def counted(db: AsyncSession, project: uuid.UUID, start: datetime, end: datetime) -> int:
        rebuilds.append((start, end))
        return await compute(db, project, start, end)

    monkeypatch.setattr(dirty_jobs, "compute_rollups", counted)
    hour = datetime(2026, 10, 20, 10, tzinfo=UTC)
    age = hour - timedelta(days=3)
    async with session_factory() as db:
        project = await create_project(db)
        await bind_project(db, project)
        await seed_traces(
            db, project, {"kept": TraceSeed(hour, (SpanSeed("llm", hour, Decimal(1), 10, 2),))}
        )
        await db.execute(
            text("""
            INSERT INTO aggregate_dirty_hours
                (project_id,bucket_hour,generation,pending_consumers,first_marked_at)
            VALUES (:p,:h,nextval('aggregate_dirty_generation_seq'),4,:age)
        """),
            {"p": project, "h": hour, "age": age},
        )
        await db.execute(
            text("""
            INSERT INTO aggregate_dirty_shards
                (project_id,bucket_hour,shard,generation,pending_consumers,first_marked_at)
            VALUES (:p,:h,0,nextval('aggregate_dirty_generation_seq'),1,:age),
                   (:p,:h,1,nextval('aggregate_dirty_generation_seq'),9,:age),
                   (:p,:h,2,nextval('aggregate_dirty_generation_seq'),0,:age)
        """),
            {"p": project, "h": hour, "age": hour},
        )
        if source_lost:
            # Hour is complete, its day is not: even disabled daily bits settle unavailable.
            await db.execute(
                text("""
                INSERT INTO aggregate_incomplete_hours(project_id,bucket_hour,reason)
                VALUES (:p,:h,'retention')
            """),
                {"p": project, "h": hour + timedelta(hours=1)},
            )
        before = await dirty_rows(db)
        await db.commit()
    async with session_factory() as db:
        observed_now = await db.scalar(text("SELECT clock_timestamp()"))
        depth, pending_age, runnable = await backlog(db, dirty.HOURLY | dirty.COST_DAILY)
        assert (depth, runnable) == (1, 1)
        assert pending_age >= (observed_now - age).total_seconds()
        scan = Scan()
        # Offered at the age of its oldest runnable row; the old-table row (attribution only)
        # is runnable only with source-loss evidence.
        offered = age if source_lost else hour
        assert await scan.next(db, dirty.HOURLY) == Candidate(project, hour, offered)
        assert await scan.next(db, dirty.HOURLY) is None
        scan.next_round()
        assert await scan.next(db, dirty.HOURLY) is None
    async with session_factory() as db:
        outcomes = await dirty_jobs._repair_marker(db, Candidate(project, hour, age), dirty.HOURLY)
        assert outcomes == (
            {"repaired", "unavailable"} if source_lost else {"repaired", "deferred"}
        )
        rows = await dirty_rows(db)
        if source_lost:
            assert rows == []
        else:
            assert [(r.shard, r.pending_consumers) for r in rows] == [(-1, 4), (1, 8)]
            assert [r.generation for r in rows] == [before[0].generation, before[2].generation]
        assert await db.scalar(text("SELECT sum(span_count) FROM span_rollups_hourly")) == 1
        # One hourly rebuild for the hour, however many rows it has.
        assert rebuilds == [(hour, hour + timedelta(hours=1))]
        await db.commit()
    await repair(session_factory, settings)
    async with session_factory() as db:
        await bind_project(db, project)
        assert await dirty_rows(db) == []


async def test_zero_mask_row_makes_disabled_pair_runnable_at_its_own_age(
    session_factory: SessionFactory,
) -> None:
    hour = datetime(2026, 10, 20, 10, tzinfo=UTC)
    async with session_factory() as db:
        project = await create_project(db)
        await bind_project(db, project)
        await db.execute(
            text("""
            INSERT INTO aggregate_dirty_hours
                (project_id,bucket_hour,generation,pending_consumers,first_marked_at)
            VALUES (:p,:h,nextval('aggregate_dirty_generation_seq'),4,:age)
        """),
            {"p": project, "h": hour, "age": hour - timedelta(days=2)},
        )
        await db.execute(
            text("""
            INSERT INTO aggregate_dirty_shards
                (project_id,bucket_hour,shard,generation,pending_consumers,first_marked_at)
            VALUES (:p,:h,0,nextval('aggregate_dirty_generation_seq'),0,:age),
                   (:p,:later,0,nextval('aggregate_dirty_generation_seq'),1,:older)
        """),
            {
                "p": project,
                "h": hour,
                "later": hour + timedelta(hours=1),
                "age": hour,
                "older": hour - timedelta(days=1),
            },
        )
        other = await create_project(db)
        await bind_project(db, other)
        await db.execute(
            text("""
            INSERT INTO aggregate_dirty_shards
                (project_id,bucket_hour,shard,generation,pending_consumers,first_marked_at)
            VALUES (:p,:h,0,nextval('aggregate_dirty_generation_seq'),1,:age)
        """),
            {"p": other, "h": hour, "age": hour - timedelta(days=1, hours=12)},
        )
        await db.commit()
    async with session_factory() as db:
        depth, _, runnable = await backlog(db, dirty.HOURLY)
        assert (depth, runnable) == (3, 3)
        scan = Scan()
        # Pairs are offered at the age of their oldest runnable row: the zero-mask shard row
        # (age `hour`) makes the first pair runnable, not its older attribution-only row.
        assert await scan.next(db, dirty.HOURLY) == Candidate(
            other, hour, hour - timedelta(days=1, hours=12)
        )
        assert await scan.next(db, dirty.HOURLY) == Candidate(
            project, hour + timedelta(hours=1), hour - timedelta(days=1)
        )
        assert await scan.next(db, dirty.HOURLY) is None
        scan.next_round()
        assert await scan.next(db, dirty.HOURLY) == Candidate(project, hour, hour)
