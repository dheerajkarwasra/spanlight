"""Calendar and replay properties exercise the registered durable daily path."""

from datetime import UTC, date, datetime, timedelta, timezone

from hypothesis import HealthCheck, given
from hypothesis import settings as examples
from hypothesis import strategies as st

from app.attribution.daily_periods import DailyPayload, expired_day, latest_expired_period
from app.config import Settings
from tests.attribution.daily_support import child, dispatch, queued, run, state, today
from tests.jobs.bypass_support import SessionFactory, execute, new_projects


@given(
    day=st.dates(min_value=date(2000, 1, 10), max_value=date(2090, 12, 1)),
    offset=st.integers(-12, 14),
    hour=st.integers(0, 23),
)
@examples(max_examples=30)
def test_utc_cutoff_preserves_whole_and_partial_day_boundaries(
    day: date, offset: int, hour: int
) -> None:
    cutoff = datetime.combine(day, datetime.min.time(), UTC) + timedelta(hours=hour)
    shifted = cutoff.astimezone(timezone(timedelta(hours=offset)))
    assert expired_day(day - timedelta(days=1), shifted)
    assert not expired_day(day, shifted)
    assert latest_expired_period(cutoff, day + timedelta(days=8)) == day + timedelta(days=1)


@given(duplicate_days=st.sets(st.integers(0, 5)), future_first=st.booleans())
@examples(
    max_examples=8, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
async def test_restarts_and_duplicate_order_keep_monotonic_six_day_cursor(
    session_factory: SessionFactory,
    settings: Settings,
    duplicate_days: set[int],
    future_first: bool,
) -> None:
    enabled = settings.model_copy(update={"attribution_enabled": True})
    [project] = await new_projects(session_factory, 1)
    for offset in range(6):
        await dispatch(session_factory, enabled, today() + timedelta(days=int(future_first)))
        job = await child(session_factory)
        assert job.payload["project_id"] == str(project)
        parsed = DailyPayload.parse(job.payload)
        assert parsed.day == today() - timedelta(days=7 - offset)
        await run(session_factory, enabled, job)
        settled = await state(session_factory, project)
        if offset in duplicate_days:
            duplicate = await queued(session_factory, job.kind, job.payload)
            await run(session_factory, enabled, duplicate)
            assert await state(session_factory, project) == settled
    final = await state(session_factory, project)
    assert final["first_period"] == today() and final["settled_period"] == today()
    assert final["settled_outcomes"] == 1 and final["next_day"] is None

    await execute(session_factory, "DELETE FROM projects WHERE id=:p", {"p": project})
