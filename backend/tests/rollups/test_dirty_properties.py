"""Generated UTC offsets and duplicate orders retain one durable obligation per UTC hour."""

import uuid
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.db.rls import bind_project
from app.rollups.dirty import ALL, mark_dirty_hours
from tests.attribution.support import SessionFactory
from tests.helpers import create_workspace
from tests.rollups.dirty_support import dirty_row


@pytest.fixture
async def dirty_property_project(browser_factory: Any) -> uuid.UUID:
    workspace = await create_workspace(await browser_factory("dirty-property@example.com"))
    return uuid.UUID(workspace.project_id)


@given(offset=st.integers(-12, 14), minute=st.integers(0, 59), repetitions=st.integers(1, 4))
@settings(
    max_examples=12, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
async def test_timezone_duplication_and_replay_preserve_hour_identity(
    dirty_property_project: uuid.UUID,
    session_factory: SessionFactory,
    offset: int,
    minute: int,
    repetitions: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.rollups.dirty.random.randrange", lambda _: 5)
    project = dirty_property_project
    hour = datetime(2026, 10, 20, 23, tzinfo=UTC)
    shifted = (hour + timedelta(minutes=minute)).astimezone(timezone(timedelta(hours=offset)))
    async with session_factory() as db:
        await bind_project(db, project)
        await mark_dirty_hours(db, project, [shifted, hour] * repetitions)
        first = await dirty_row(db)
        await mark_dirty_hours(db, project, [hour, shifted] * repetitions)
        second = await dirty_row(db)
        assert first.bucket_hour == second.bucket_hour == hour
        assert first.pending_consumers == second.pending_consumers == ALL
        assert first.first_marked_at == second.first_marked_at
        assert first.generation != second.generation
