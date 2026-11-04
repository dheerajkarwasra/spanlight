"""Real database faults and delays validate atomic repair and the configured time budgets."""

import time
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.config import Settings
from app.db.rls import bind_project, bypass_rls
from app.jobs.context import TaskContext
from app.jobs.queue import claim_next, enqueue
from app.jobs.tasks import TASKS
from app.rollups.dirty import mark_dirty_hours
from tests.attribution.support import SessionFactory, SpanSeed, TraceSeed, seed_traces
from tests.rls.factories import create_project
from tests.rollups.dirty_support import dirty_count, dirty_row, set_dirty_pending
from tests.rollups.test_dirty_repair import repair

pytestmark = pytest.mark.serial


@pytest.mark.parametrize("fault", ["RAISE EXCEPTION 'repair test fault'", "PERFORM pg_sleep(6)"])
async def test_database_error_or_marker_timeout_rolls_back_and_advances(
    session_factory: SessionFactory,
    settings: Settings,
    fault: str,
) -> None:
    hour = datetime(2026, 10, 20, 10, tzinfo=UTC)
    async with session_factory() as db:
        broken = await create_project(db)
        await bind_project(db, broken)
        await seed_traces(
            db, broken, {"old": TraceSeed(hour, (SpanSeed("llm", hour, Decimal(1), 10, 2),))}
        )
        await mark_dirty_hours(db, broken, [hour])
        healthy = await create_project(db)
        await bind_project(db, healthy)
        await mark_dirty_hours(db, healthy, [hour])
        await db.execute(
            text(f"""
            CREATE FUNCTION repair_test_fault() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN IF NEW.project_id = '{broken}'::uuid THEN {fault}; END IF;
            RETURN NEW; END $$
        """)
        )
        await db.execute(
            text(
                "CREATE TRIGGER repair_test_fault BEFORE INSERT ON cost_rollups_daily "
                "FOR EACH ROW EXECUTE FUNCTION repair_test_fault()"
            )
        )
        await db.commit()
    try:
        started = time.monotonic()
        await repair(session_factory, settings)
        assert time.monotonic() - started < 8
        async with session_factory() as db:
            await bind_project(db, broken)
            assert await db.scalar(text("SELECT count(*) FROM span_rollups_hourly")) == 0
            assert (await dirty_row(db)).pending_consumers == 15
            await bind_project(db, healthy)
            assert await dirty_count(db) == 0
    finally:
        async with session_factory() as db:
            await db.execute(text("DROP TRIGGER repair_test_fault ON cost_rollups_daily"))
            await db.execute(text("DROP FUNCTION repair_test_fault()"))
            await db.commit()
    await repair(session_factory, settings)
    async with session_factory() as db:
        await bind_project(db, broken)
        assert await dirty_count(db) == 0


async def test_no_marker_starts_after_thirty_second_budget(
    session_factory: SessionFactory,
    settings: Settings,
) -> None:
    hour = datetime(2026, 10, 20, 10, tzinfo=UTC)
    async with session_factory() as db:
        for _ in range(8):
            project = await create_project(db)
            await bind_project(db, project)
            await seed_traces(
                db, project, {"old": TraceSeed(hour, (SpanSeed("llm", hour, Decimal(1), 10, 2),))}
            )
            await mark_dirty_hours(db, project, [hour])
            await set_dirty_pending(db, 1)
        await db.execute(
            text("""CREATE FUNCTION repair_test_delay() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN PERFORM pg_sleep(4.6); RETURN NEW; END $$""")
        )
        await db.execute(
            text(
                "CREATE TRIGGER repair_test_delay BEFORE INSERT ON span_rollups_hourly "
                "FOR EACH ROW EXECUTE FUNCTION repair_test_delay()"
            )
        )
        await db.commit()
    try:
        started = time.monotonic()
        await repair(session_factory, settings)
        elapsed = time.monotonic() - started
        assert 30 <= elapsed < 39
        async with session_factory() as db:
            await bypass_rls(db)
            assert await dirty_count(db) == 1
            assert await db.scalar(text("SELECT count(*) FROM span_rollups_hourly")) == 7
    finally:
        async with session_factory() as db:
            await db.execute(text("DROP TRIGGER repair_test_delay ON span_rollups_hourly"))
            await db.execute(text("DROP FUNCTION repair_test_delay()"))
            await db.commit()


async def test_outer_deadline_cancels_blocked_heartbeat_before_any_marker(
    session_factory: SessionFactory,
    settings: Settings,
) -> None:
    hour = datetime(2026, 10, 20, 10, tzinfo=UTC)
    async with session_factory() as db:
        project = await create_project(db)
        await bind_project(db, project)
        await mark_dirty_hours(db, project, [hour])
        await enqueue(db, "repair_aggregates", max_attempts=1)
        await db.commit()
        job = await claim_next(db)
        assert job is not None
        await db.execute(
            text("""CREATE FUNCTION repair_test_heartbeat() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN PERFORM pg_sleep(45); RETURN NEW; END $$""")
        )
        await db.execute(
            text(
                "CREATE TRIGGER repair_test_heartbeat BEFORE UPDATE ON jobs "
                "FOR EACH ROW EXECUTE FUNCTION repair_test_heartbeat()"
            )
        )
        await db.commit()
    try:
        started = time.monotonic()
        await TASKS["repair_aggregates"](
            TaskContext(
                job, settings.model_copy(update={"aggregate_repair_enabled": True}), session_factory
            ),
            {},
        )
        elapsed = time.monotonic() - started
        assert 39 <= elapsed < 44
        async with session_factory() as db:
            await bind_project(db, project)
            assert (await dirty_row(db)).pending_consumers == 15
    finally:
        async with session_factory() as db:
            await db.execute(text("DROP TRIGGER repair_test_heartbeat ON jobs"))
            await db.execute(text("DROP FUNCTION repair_test_heartbeat()"))
            await db.commit()
