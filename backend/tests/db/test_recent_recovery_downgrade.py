"""The same guarded schema transition refuses backlog and accepts supported recovery."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import DBAPIError

from app.config import Settings
from app.rollups.compute import compute_rollups, floor_hour
from tests.attribution.daily_support import child, dispatch, run
from tests.db.migration_support import downgrade_to, upgrade_to_target
from tests.jobs.bypass_support import SessionFactory, execute, fetch, new_projects, seed_llm_spans
from tests.rollups.recent_support import state
from tests.rollups.test_recent_recovery import cli_recover


@pytest.mark.migration_revision("1029")
async def test_current_newest_row_refuses_then_supported_cli_and_daily_drain_permit(
    migration_database_url: str,
    migration_session_factory: SessionFactory,
    settings: Settings,
) -> None:
    factory = migration_session_factory
    [project] = await new_projects(factory, 1)
    now = datetime.now(UTC)
    target = floor_hour(now - timedelta(minutes=10))
    before = await state(factory, project)
    assert before["next_closed_hour"] < target
    await seed_llm_spans(factory, project, 2, started_at=now)
    async with factory() as db:
        await compute_rollups(db, project, floor_hour(now), floor_hour(now) + timedelta(hours=1))
        await db.commit()
    await execute(
        factory,
        "INSERT INTO "
        "aggregate_dirty_hours(project_id,bucket_hour,generation,pending_consumers) "
        "VALUES(:p,:h,42,15)",
        {"p": project, "h": before["next_closed_hour"]},
    )
    await execute(
        factory,
        "INSERT INTO aggregate_incomplete_hours(project_id,bucket_hour,reason) "
        "VALUES(:p,:h,'erasure')",
        {"p": project, "h": before["next_closed_hour"]},
    )
    await execute(
        factory,
        "UPDATE projects SET budget_history_missing_through=:h WHERE id=:p",
        {"p": project, "h": before["next_closed_hour"]},
    )
    dirty = await fetch(factory, "SELECT * FROM aggregate_dirty_hours")
    coverage = await fetch(factory, "SELECT * FROM aggregate_incomplete_hours")
    financial = await fetch(factory, "SELECT id,budget_history_missing_through FROM projects")
    enabled = settings.model_copy(update={"attribution_enabled": True})
    for _ in range(6):
        await dispatch(factory, enabled)
        await run(factory, enabled, await child(factory))
    assert await fetch(factory, "SELECT * FROM jobs WHERE status IN ('queued','running')") == []
    # The daily condition is settled already: this refusal specifically observes recent backlog.
    with pytest.raises(DBAPIError, match="Unfinished aggregate scheduling"):
        await asyncio.to_thread(downgrade_to, migration_database_url, "1028")
    assert (await state(factory, project))["next_closed_hour"] == before["next_closed_hour"]
    code, output = await cli_recover(
        migration_database_url, project, before["next_closed_hour"], target
    )
    assert code == 0, output
    assert (await state(factory, project))["next_closed_hour"] == target
    await asyncio.to_thread(downgrade_to, migration_database_url, "1028")
    assert await fetch(factory, "SELECT to_regclass('aggregate_job_progress') AS progress") == [
        {"progress": None}
    ]
    assert await fetch(factory, "SELECT * FROM aggregate_dirty_hours") == dirty
    assert await fetch(factory, "SELECT * FROM aggregate_incomplete_hours") == coverage
    assert (
        await fetch(factory, "SELECT id,budget_history_missing_through FROM projects") == financial
    )
    await asyncio.to_thread(upgrade_to_target, migration_database_url)
