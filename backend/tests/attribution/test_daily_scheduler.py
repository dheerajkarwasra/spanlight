"""Daily dispatcher payload and dedupe use the scheduled UTC instant."""

from datetime import UTC, datetime, timedelta, timezone

from app.config import Settings
from app.jobs.scheduler import schedule_periodic
from tests.jobs.bypass_support import SessionFactory, fetch


async def test_offset_views_of_same_slot_have_one_utc_daily_horizon(
    session_factory: SessionFactory,
    settings: Settings,
) -> None:
    instant = datetime(2026, 10, 29, 23, 4, tzinfo=UTC)
    enabled = settings.model_copy(update={"attribution_enabled": True})
    async with session_factory() as db:
        await schedule_periodic(db, enabled, instant)
        await schedule_periodic(db, enabled, instant.astimezone(timezone(timedelta(hours=2))))
    rows = await fetch(
        session_factory,
        "SELECT payload,max_attempts FROM jobs WHERE kind='schedule_attribution_daily'",
    )
    assert rows == [{"payload": {"scheduled_period": "2026-10-29"}, "max_attempts": 1}]


async def test_disabled_attribution_does_not_schedule_daily_dispatcher(
    session_factory: SessionFactory,
    settings: Settings,
) -> None:
    async with session_factory() as db:
        await schedule_periodic(
            db, settings.model_copy(update={"attribution_enabled": False}), datetime.now(UTC)
        )
    assert (
        await fetch(
            session_factory,
            "SELECT kind FROM jobs "
            "WHERE kind IN ('refresh_attribution','schedule_attribution_daily')",
        )
        == []
    )
