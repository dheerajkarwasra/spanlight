"""The Doctor health tile reads the Overview's own KPIs, like every other Overview tile.

Failure modes pinned here: a closed hour the rollup job has not reached yet, and an hour awaiting
aggregate repair (repair is off by default), both read as "no score" when the tile used the
alert-grade reader.
"""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from app.api.window import TimeWindow
from app.db.rls import bind_project
from app.insights.health_service import project_health
from app.rollups.compute import floor_hour
from app.rollups.dirty import mark_dirty_hours
from tests.helpers import create_workspace
from tests.jobs.bypass_support import SessionFactory, seed_llm_spans
from tests.rollups.error_support import compute


async def _seed(
    browser_factory: Any, session_factory: SessionFactory, email: str
) -> tuple[UUID, TimeWindow]:
    """Three calls in the hour before the fixed split, with no rollup row for the project."""
    workspace = await create_workspace(await browser_factory(email))
    project = UUID(workspace.project_id)
    hour = floor_hour(datetime.now(UTC)) - timedelta(hours=2)
    await seed_llm_spans(session_factory, project, 3, started_at=hour + timedelta(minutes=5))
    return project, TimeWindow(hour, hour + timedelta(hours=1))


async def test_health_counts_previous_hour_without_rollup(
    browser_factory: Any, session_factory: SessionFactory
) -> None:
    project, window = await _seed(browser_factory, session_factory, "health-split@example.com")
    async with session_factory() as db:
        await bind_project(db, project)
        health = await project_health(db, project, window, None)
    assert health.score is not None
    assert health.approximate is False


async def test_health_scores_while_an_hour_awaits_repair(
    browser_factory: Any, session_factory: SessionFactory
) -> None:
    """Ingest marks its hour dirty; with aggregate repair off (the default) the mark stays. The
    tile must still score the calls, as the other Overview tiles show them, not read "—"."""
    workspace = await create_workspace(await browser_factory("health-pending@example.com"))
    project = UUID(workspace.project_id)
    # Older than the 24 h raw tail, so every reader takes this hour from its rollup row.
    hour = floor_hour(datetime.now(UTC)) - timedelta(hours=30)
    window = TimeWindow(hour, hour + timedelta(hours=1))
    await seed_llm_spans(session_factory, project, 3, started_at=hour + timedelta(minutes=5))
    await compute(session_factory, project, hour, hour + timedelta(hours=1))
    async with session_factory() as db:
        await bind_project(db, project)
        await mark_dirty_hours(db, project, [hour])
        await db.commit()
    async with session_factory() as db:
        await bind_project(db, project)
        health = await project_health(db, project, window, None)
    assert health.score is not None
