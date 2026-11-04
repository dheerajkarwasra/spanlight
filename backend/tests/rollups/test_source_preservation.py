"""Source loss must preserve each previously materialized aggregate."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.attribution.refresh import refresh_attribution
from app.db.rls import bypass_rls
from app.end_users.erasure_job import erase_end_user
from app.end_users.refresh import refresh_user_stats
from app.reconciliation.rollups import refresh_cost_rollups
from app.rollups.compute import compute_rollups
from tests.attribution.support import SessionFactory, SpanSeed, TraceSeed, seed_traces
from tests.end_users.test_erasure import _seed_user
from tests.helpers import create_workspace
from tests.jobs.retention_support import apply_retention
from tests.rls.factories import create_project

DAY = datetime(2026, 9, 1, tzinfo=UTC)


def trace_at(start: datetime, cost: str, *, user: str = "kept") -> TraceSeed:
    return TraceSeed(
        start,
        (SpanSeed("llm", start, Decimal(cost), 100, 20),),
        name="answer",
        external_user_id=user,
    )


async def refresh_all(factory: SessionFactory, project: uuid.UUID) -> None:
    async with factory() as db:
        await bypass_rls(db)
        await compute_rollups(db, project, DAY, DAY + timedelta(days=2))
        await refresh_cost_rollups(db, project, [DAY.date(), (DAY + timedelta(days=1)).date()])
        await refresh_user_stats(db, project, [DAY.date(), (DAY + timedelta(days=1)).date()])
        await refresh_attribution(db, project, [], [DAY.date(), (DAY + timedelta(days=1)).date()])
        await db.commit()


async def totals(factory: SessionFactory, project: uuid.UUID) -> tuple[object, ...]:
    async with factory() as db:
        await bypass_rls(db)
        return tuple(
            (
                await db.execute(
                    text("""
            SELECT
              (SELECT sum(cost_usd) FROM span_rollups_hourly WHERE project_id = :id),
              (SELECT sum(cost_usd) FROM cost_rollups_daily WHERE project_id = :id),
              (SELECT sum(cost_usd) FROM attribution_rollups_daily
               WHERE project_id = :id AND group_kind = 'trace_name'),
              (SELECT sum(cost_usd) FROM user_stats_daily WHERE project_id = :id)
        """),
                    {"id": project},
                )
            ).one()
        )


async def test_partial_hour_and_day_retention_preserve_snapshots(
    session_factory: SessionFactory,
) -> None:
    async with session_factory() as db:
        await bypass_rls(db)
        project = await create_project(db)
        await seed_traces(
            db,
            project,
            {
                "expired": trace_at(DAY + timedelta(hours=10, minutes=5), "1.00"),
                "kept": trace_at(DAY + timedelta(hours=10, minutes=45), "2.00"),
            },
        )
        await db.commit()
    await refresh_all(session_factory, project)
    assert await totals(session_factory, project) == (Decimal("3.00"),) * 4
    assert (
        await apply_retention(session_factory, now=DAY + timedelta(days=30, hours=10, minutes=30))
        == 1
    )
    await refresh_all(session_factory, project)
    assert await totals(session_factory, project) == (Decimal("3.00"),) * 4


async def test_retention_marks_recent_children_of_an_old_trace(
    session_factory: SessionFactory,
) -> None:
    async with session_factory() as db:
        await bypass_rls(db)
        project = await create_project(db)
        await seed_traces(
            db,
            project,
            {
                "long": TraceSeed(
                    DAY,
                    (
                        SpanSeed("llm", DAY, Decimal("1.00"), 100, 20),
                        SpanSeed("llm", DAY + timedelta(days=1), Decimal("2.00"), 100, 20),
                    ),
                ),
                "kept": trace_at(DAY + timedelta(days=1, minutes=5), "4.00"),
            },
        )
        await db.commit()
    await refresh_all(session_factory, project)
    assert await apply_retention(session_factory, now=DAY + timedelta(days=30, hours=12)) == 1
    await refresh_all(session_factory, project)
    async with session_factory() as db:
        await bypass_rls(db)
        cost = await db.scalar(
            text("""
            SELECT sum(cost_usd) FROM span_rollups_hourly
            WHERE project_id = :id AND bucket_start = :hour
        """),
            {"id": project, "hour": DAY + timedelta(days=1)},
        )
    assert cost == Decimal("6.00")


async def test_erasure_preserves_anonymous_snapshots_and_removes_named_rows(
    session_factory: SessionFactory,
) -> None:
    async with session_factory() as db:
        await bypass_rls(db)
        project = await create_project(db)
        await seed_traces(
            db,
            project,
            {
                "erased": trace_at(DAY + timedelta(hours=10), "1.00", user="erased"),
                "kept": trace_at(DAY + timedelta(hours=10), "2.00"),
            },
        )
        await db.commit()
    await refresh_all(session_factory, project)
    await erase_end_user(session_factory, project_id=project, external_user_id="erased", counts={})
    await refresh_all(session_factory, project)
    assert await totals(session_factory, project) == (
        Decimal("3.00"),
        Decimal("3.00"),
        Decimal("3.00"),
        Decimal("2.00"),
    )
    async with session_factory() as db:
        await bypass_rls(db)
        named = await db.scalar(
            text("""
            SELECT count(*) FROM attribution_rollups_daily
            WHERE project_id = :id AND group_kind = 'user' AND group_value = 'erased'
        """),
            {"id": project},
        )
    assert named == 0


async def test_erasure_marks_experiment_copy_hours(
    browser_factory,
    client,
    session_factory: SessionFactory,
) -> None:

    workspace = await create_workspace(await browser_factory("copies@example.com"))
    ids = await _seed_user(client, session_factory, workspace, "erased")
    project = uuid.UUID(workspace.project_id)
    now = datetime.now(UTC)
    # Two hours before the erased user's trace (started five minutes ago), never its hour; the
    # day's midnight was that hour between 00:00 and 01:00 UTC.
    start = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=2)
    async with session_factory() as db:
        await bypass_rls(db)
        await db.execute(
            text("""
            UPDATE traces SET started_at = :hour, ended_at = :hour
            WHERE project_id = :id AND trace_id = :trace
        """),
            {"id": project, "trace": ids["run_trace"], "hour": start},
        )
        await compute_rollups(db, project, start, start + timedelta(days=1))
        await db.commit()
    await erase_end_user(session_factory, project_id=project, external_user_id="erased", counts={})
    async with session_factory() as db:
        await compute_rollups(db, project, start, start + timedelta(days=1))
        await db.commit()
        await bypass_rls(db)
        preserved = await db.scalar(
            text("""
            SELECT sum(traces) FROM trace_rollups_hourly
            WHERE project_id = :id AND bucket_start = :hour
        """),
            {"id": project, "hour": start},
        )
    # The copied experiment trace has no named user, but its anonymous count survives.
    assert preserved == 1


@pytest.mark.parametrize("deletion_first", [False, True])
async def test_recompute_and_erasure_serialize_without_shrinking_snapshots(
    session_factory: SessionFactory,
    deletion_first: bool,
) -> None:

    async with session_factory() as db:
        await bypass_rls(db)
        project = await create_project(db)
        await seed_traces(
            db,
            project,
            {
                "erased": trace_at(DAY + timedelta(hours=10), "1.00", user="erased"),
                "kept": trace_at(DAY + timedelta(hours=10), "2.00"),
            },
        )
        await db.commit()
    await refresh_all(session_factory, project)

    async def wait_for_block(query: str) -> None:
        async with asyncio.timeout(5):
            async with session_factory() as observer:
                while True:
                    await observer.execute(text("SELECT pg_stat_clear_snapshot()"))
                    blocked = await observer.scalar(
                        text("""
                        SELECT count(*) FROM pg_stat_activity
                        WHERE datname = current_database() AND wait_event_type = 'Lock'
                          AND query LIKE :query
                    """),
                        {"query": query},
                    )
                    if blocked:
                        return
                    await asyncio.sleep(0.01)

    erase = None
    refresh = None
    async with session_factory() as blocker:
        await bypass_rls(blocker)
        if deletion_first:
            await blocker.execute(
                text("""
                SELECT trace_id FROM traces WHERE project_id = :id FOR UPDATE
            """),
                {"id": project},
            )
        else:
            await compute_rollups(blocker, project, DAY, DAY + timedelta(days=1))
        erase = asyncio.create_task(
            erase_end_user(
                session_factory, project_id=project, external_user_id="erased", counts={}
            )
        )
        try:
            if deletion_first:
                await wait_for_block("%traces%")
                refresh = asyncio.create_task(refresh_all(session_factory, project))
            await wait_for_block("%pg_advisory_xact_lock%")
        finally:
            await blocker.commit()
            await asyncio.wait_for(
                asyncio.gather(
                    erase,
                    *([refresh] if refresh is not None else []),
                ),
                timeout=10,
            )
    await refresh_all(session_factory, project)
    assert await totals(session_factory, project) == (
        Decimal("3.00"),
        Decimal("3.00"),
        Decimal("3.00"),
        Decimal("2.00"),
    )
