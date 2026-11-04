"""Pending age follows the relevant consumer rows across both dirty tables (D4)."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from app.db.rls import bind_project
from app.rollups.coverage_reads import CoverageState, read_covered
from app.rollups.dirty import ATTRIBUTION, COST_DAILY, HOURLY
from tests.attribution.support import SessionFactory
from tests.rls.factories import create_project


async def test_pending_age_uses_only_relevant_rows_from_both_tables(
    session_factory: SessionFactory,
) -> None:
    hour = datetime(2026, 10, 20, 10, tzinfo=UTC)
    monday, tuesday, wednesday = hour - timedelta(days=2), hour - timedelta(days=1), hour
    async with session_factory() as db:
        project = await create_project(db)
        await bind_project(db, project)
        await db.execute(
            text("""
            INSERT INTO aggregate_dirty_hours
                (project_id,bucket_hour,generation,pending_consumers,first_marked_at)
            VALUES (:p,:h,nextval('aggregate_dirty_generation_seq'),4,:age)
        """),
            {"p": project, "h": hour, "age": monday},
        )
        await db.execute(
            text("""
            INSERT INTO aggregate_dirty_shards
                (project_id,bucket_hour,shard,generation,pending_consumers,first_marked_at)
            VALUES (:p,:h,0,nextval('aggregate_dirty_generation_seq'),1,:wednesday),
                   (:p,:h,1,nextval('aggregate_dirty_generation_seq'),2,:tuesday)
        """),
            {"p": project, "h": hour, "wednesday": wednesday, "tuesday": tuesday},
        )
        hourly = CoverageState()
        assert (
            await read_covered(
                db,
                text("SELECT 1 WHERE false"),
                {},
                project,
                hour,
                hour + timedelta(hours=1),
                hourly,
                relevant_consumers=HOURLY,
            )
            == []
        )
        assert hourly.metadata() == {"aggregate_status": "pending", "oldest_pending_at": wednesday}
        daily = CoverageState()
        await read_covered(
            db,
            text("SELECT 1"),
            {},
            project,
            hour,
            hour + timedelta(hours=1),
            daily,
            relevant_consumers=COST_DAILY | ATTRIBUTION,
        )
        assert daily.metadata() == {"aggregate_status": "pending", "oldest_pending_at": monday}
        await db.execute(text("UPDATE aggregate_dirty_hours SET pending_consumers=5"))
        collided = CoverageState()
        await read_covered(
            db,
            text("SELECT 1"),
            {},
            project,
            hour,
            hour + timedelta(hours=1),
            collided,
            relevant_consumers=HOURLY,
        )
        assert collided.oldest_pending_at == monday
        await db.execute(text("UPDATE aggregate_dirty_hours SET pending_consumers=4"))
        await db.execute(text("UPDATE aggregate_dirty_shards SET pending_consumers=0"))
        complete = CoverageState()
        await read_covered(
            db,
            text("SELECT 1"),
            {},
            project,
            hour,
            hour + timedelta(hours=1),
            complete,
            relevant_consumers=HOURLY,
        )
        assert complete.metadata() == {"aggregate_status": "complete", "oldest_pending_at": None}
