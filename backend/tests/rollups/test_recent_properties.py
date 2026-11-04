"""Generated calendar/restart/source patterns exercise real registered jobs."""

from datetime import UTC, datetime, timedelta, timezone

from hypothesis import HealthCheck, given
from hypothesis import settings as examples
from hypothesis import strategies as st

from app.config import Settings
from app.rollups.compute import floor_hour
from tests.jobs.bypass_support import SessionFactory, execute, fetch, new_projects, seed_llm_spans
from tests.rollups.recent_support import admit_at, run_recent, state


@given(
    offset=st.integers(-12, 14),
    empty=st.booleans(),
    unavailable=st.booleans(),
    duplicates=st.integers(0, 2),
)
@examples(
    max_examples=12, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
async def test_restarts_offsets_empty_and_unavailable_preserve_contiguous_prefix(
    session_factory: SessionFactory,
    settings: Settings,
    offset: int,
    empty: bool,
    unavailable: bool,
    duplicates: int,
) -> None:
    [project] = await new_projects(session_factory, 1)
    now = datetime.now(UTC)
    start = floor_hour(now) - timedelta(hours=51)
    await admit_at(session_factory, project, start)
    if not empty:
        await seed_llm_spans(session_factory, project, 3, started_at=start + timedelta(hours=4))
    if unavailable:
        await execute(
            session_factory,
            "INSERT INTO aggregate_incomplete_hours(project_id,bucket_hour,reason) "
            "VALUES(:p,:h,'retention')",
            {"p": project, "h": start + timedelta(hours=1)},
        )
    evidence = await fetch(
        session_factory,
        "SELECT * FROM aggregate_incomplete_hours WHERE project_id=:p",
        {"p": project},
    )
    await run_recent(session_factory, settings, now.astimezone(timezone(timedelta(hours=offset))))
    assert (await state(session_factory, project))["next_closed_hour"] == start + timedelta(hours=3)
    await run_recent(session_factory, settings, now + timedelta(hours=1))
    assert (await state(session_factory, project))["next_closed_hour"] == start + timedelta(hours=6)
    for _ in range(duplicates):
        await run_recent(session_factory, settings, now)
    assert (await state(session_factory, project))["next_closed_hour"] == start + timedelta(
        hours=6 + 3 * duplicates
    )
    rows = await fetch(
        session_factory,
        "SELECT sum(span_count) n FROM span_rollups_hourly WHERE project_id=:p",
        {"p": project},
    )
    assert rows == [{"n": None if empty else 3}]
    assert (
        await fetch(
            session_factory,
            "SELECT * FROM aggregate_incomplete_hours WHERE project_id=:p",
            {"p": project},
        )
        == evidence
    )
    await execute(session_factory, "DELETE FROM projects WHERE id=:p", {"p": project})
