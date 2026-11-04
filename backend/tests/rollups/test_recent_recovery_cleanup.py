"""The CLI-owned engine finishes rollback and disposal before returning or propagating
cancellation."""

import asyncio
from datetime import UTC, datetime, timedelta
from time import monotonic

import pytest
from sqlalchemy import text

from app.rollups.compute import floor_hour
from app.rollups.recovery import recover_recent
from tests.jobs.bypass_support import SessionFactory, fetch, new_projects
from tests.rollups.recent_support import admit_at, state


async def connections(factory: SessionFactory) -> int:
    return (
        await fetch(
            factory,
            "SELECT count(*) n FROM pg_stat_activity"
            " WHERE datname=current_database() AND usename=current_user",
        )
    )[0]["n"]


async def test_owned_recovery_returns_after_connection_disposal(
    session_factory: SessionFactory, app_database_url: str
) -> None:
    [project] = await new_projects(session_factory, 1)
    start = floor_hour(datetime.now(UTC)) - timedelta(hours=50)
    await admit_at(session_factory, project, start)
    before = await connections(session_factory)
    result = await recover_recent(app_database_url, project, start, start + timedelta(hours=6))
    assert result.status == "complete" and result.chunks == 2
    assert (await state(session_factory, project))["next_closed_hour"] == result.cursor
    assert await connections(session_factory) == before


@pytest.mark.serial
async def test_cancellation_rolls_back_closure_and_disposes_owned_connection(
    session_factory: SessionFactory, app_database_url: str
) -> None:
    [project] = await new_projects(session_factory, 1)
    start = floor_hour(datetime.now(UTC)) - timedelta(hours=50)
    await admit_at(session_factory, project, start)
    before = await state(session_factory, project)
    async with session_factory() as holder:
        await holder.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
            {"key": f"aggregate-source-loss:{project}"},
        )
        connection_count = await connections(session_factory)
        task = asyncio.create_task(
            recover_recent(app_database_url, project, start, start + timedelta(hours=6))
        )
        async with asyncio.timeout(0.2):
            while not (  # noqa: ASYNC110 - observe the actual PostgreSQL lock wait.
                await fetch(
                    session_factory,
                    "SELECT count(*) n FROM pg_locks WHERE locktype='advisory' AND NOT granted",
                )
            )[0]["n"]:
                await asyncio.sleep(0.005)
        started = monotonic()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        elapsed = monotonic() - started
        assert await connections(session_factory) == connection_count
        assert await state(session_factory, project) == before
        assert elapsed < 1
        print("CALIBRATION recovery_cancel_and_dispose_seconds=", elapsed)
