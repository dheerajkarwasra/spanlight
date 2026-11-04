"""Fresh current rows cannot certify an older admitted closed-hour gap."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from app.db.rls import bind_project
from app.rollups.compute import compute_rollups, floor_hour
from app.rollups.coverage_reads import CoverageState, read_covered
from app.rollups.dirty import HOURLY
from app.rollups.queries import RAW_TAIL_MAX, read_split
from tests.jobs.bypass_support import SessionFactory, execute, new_projects, seed_llm_spans
from tests.rollups.recent_support import admit_at


@pytest.mark.parametrize("gap_hours", [4, 50])
async def test_newest_computed_at_does_not_hide_unfinished_closed_interval(
    session_factory: SessionFactory, gap_hours: int
) -> None:
    [project] = await new_projects(session_factory, 1)
    now = datetime.now(UTC)
    hour = floor_hour(now) - timedelta(hours=gap_hours)
    await admit_at(session_factory, project, hour)
    await seed_llm_spans(session_factory, project, 7, started_at=hour)
    await seed_llm_spans(session_factory, project, 1, started_at=now)
    async with session_factory() as db:
        await compute_rollups(db, project, floor_hour(now), floor_hour(now) + timedelta(hours=1))
        await db.commit()
    status = CoverageState()
    async with session_factory() as db:
        await bind_project(db, project)
        await read_covered(
            db,
            text(
                "SELECT sum(span_count) AS n FROM span_rollups_hourly WHERE project_id=:p "
                "AND bucket_start=:h"
            ),
            {"p": project, "h": hour},
            project,
            hour,
            hour + timedelta(hours=1),
            status,
            relevant_consumers=HOURLY,
        )
    assert status.status == "pending"
    assert status.oldest_pending_at == hour + timedelta(hours=1, minutes=10)


@pytest.mark.parametrize("mask", [0, 2, 4, 8, 1, 15])
async def test_only_consumed_hourly_intersection_adds_pending(
    session_factory: SessionFactory, mask: int
) -> None:
    [project] = await new_projects(session_factory, 1)
    start = floor_hour(datetime.now(UTC)) - timedelta(hours=50)
    await admit_at(session_factory, project, start)
    status = CoverageState()
    async with session_factory() as db:
        await bind_project(db, project)
        await read_covered(
            db,
            text("SELECT 1 AS value"),
            {},
            project,
            start,
            start + timedelta(hours=1),
            status,
            relevant_consumers=mask,
        )
        assert status.status == ("pending" if mask & 1 else "complete")
        # Raw authority adds no scheduling pending and cannot erase previous pending state.
        await read_covered(
            db,
            text("SELECT 1 AS value"),
            {},
            project,
            start,
            start + timedelta(hours=1),
            status,
            relevant_consumers=0,
        )
        assert status.status == ("pending" if mask & 1 else "complete")
        earlier = CoverageState()
        await read_covered(
            db,
            text("SELECT 1 WHERE false"),
            {},
            project,
            start - timedelta(days=1),
            start,
            earlier,
            relevant_consumers=HOURLY,
        )
        assert earlier.status == "complete" and earlier.oldest_pending_at is None


async def test_raw_tail_bound_cannot_certify_unfinished_materialization(
    session_factory: SessionFactory,
) -> None:

    [project] = await new_projects(session_factory, 1)
    now = datetime.now(UTC)
    start = floor_hour(now) - timedelta(hours=50)
    await admit_at(session_factory, project, start)
    await seed_llm_spans(session_factory, project, 1, started_at=now)
    async with session_factory() as db:
        await compute_rollups(db, project, floor_hour(now), floor_hour(now) + timedelta(hours=1))
        await db.commit()
        await bind_project(db, project)
        split = await read_split(db, project, now)
        assert split == floor_hour(now - RAW_TAIL_MAX)
        status = CoverageState()
        await read_covered(
            db, text("SELECT 1"), {}, project, start, split, status, relevant_consumers=HOURLY
        )
        assert status.status == "pending"


async def test_missing_expected_progress_raises_instead_of_fabricating_completion(
    session_factory: SessionFactory,
) -> None:

    [project] = await new_projects(session_factory, 1)
    # Deliberate admission corruption is not recovery or fixture completion.
    await execute(
        session_factory,
        "DELETE FROM aggregate_job_progress WHERE project_id=:p AND kind='rollup_recent'",
        {"p": project},
    )
    now = datetime.now(UTC)
    async with session_factory() as db:
        await bind_project(db, project)
        with pytest.raises(RuntimeError, match="admission is missing"):
            await read_covered(
                db,
                text("SELECT 1"),
                {},
                project,
                now - timedelta(days=1),
                now,
                CoverageState(),
                relevant_consumers=HOURLY,
            )
        with pytest.raises(RuntimeError, match="admission is missing"):
            await read_split(db, project, now)
