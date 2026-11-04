"""Generated duplicate/order/restart cases use real sources and all four stored products."""

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

from hypothesis import HealthCheck, given
from hypothesis import settings as hypothesis_settings
from hypothesis import strategies as st
from sqlalchemy import text

from app.config import Settings
from app.db.rls import bind_project
from app.rollups.coverage import complete_day_ranges, complete_ranges, lock_aggregate_sources
from app.rollups.dirty import mark_dirty_hours
from tests.attribution.support import SessionFactory, SpanSeed, TraceSeed, seed_traces
from tests.rls.factories import create_project
from tests.rollups.dirty_support import dirty_count
from tests.rollups.test_dirty_repair import repair


@given(offset=st.integers(-12, 14), copies=st.integers(1, 4), reverse=st.booleans())
@hypothesis_settings(
    max_examples=8, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
async def test_generated_duplication_order_timezone_and_restart_invariance(
    session_factory: SessionFactory,
    settings: Settings,
    offset: int,
    copies: int,
    reverse: bool,
) -> None:
    hour = datetime(2026, 10, 20, 23, tzinfo=UTC)
    later = hour + timedelta(hours=2)
    hours = [hour.astimezone(timezone(timedelta(hours=offset))), later] * copies
    if reverse:
        hours.reverse()
    async with session_factory() as db:
        project = await create_project(db)
        await bind_project(db, project)
        await seed_traces(
            db,
            project,
            {
                "a": TraceSeed(
                    hour, (SpanSeed("llm", hour, Decimal(1), 10, 2),), external_user_id="u"
                ),
                "b": TraceSeed(
                    later, (SpanSeed("llm", later, Decimal(2), 20, 4),), external_user_id="v"
                ),
            },
        )
        await mark_dirty_hours(db, project, hours)
        await db.commit()
    await repair(session_factory, settings, attribution_enabled=False, user_stats_enabled=False)
    await repair(session_factory, settings)
    await repair(session_factory, settings)
    async with session_factory() as db:
        await lock_aggregate_sources(db, project)
        assert await complete_ranges(db, project, hour, hour + timedelta(hours=1)) == [
            (hour, hour + timedelta(hours=1))
        ]
        assert await complete_day_ranges(db, project, [hour.date(), later.date()]) == [
            (hour.date(), later.date())
        ]
        # Shadow comparisons are confined to the proven-complete source days above.
        raw = (
            await db.execute(
                text("SELECT sum(cost_usd),sum(input_tokens+output_tokens),count(*) FROM spans")
            )
        ).one()
        assert raw == (Decimal(3), 36, 2)
        assert (
            await db.execute(
                text(
                    "SELECT sum(cost_usd),sum(input_tokens+output_tokens),sum(span_count) "
                    "FROM span_rollups_hourly"
                )
            )
        ).one() == raw
        assert await db.scalar(text("SELECT sum(cost_usd) FROM cost_rollups_daily")) == raw[0]
        assert (
            await db.scalar(
                text("SELECT sum(cost_usd) FROM attribution_rollups_daily WHERE group_kind='user'")
            )
            == raw[0]
        )
        assert (
            await db.execute(
                text("SELECT sum(cost_usd),sum(tokens),sum(llm_calls) FROM user_stats_daily")
            )
        ).one() == raw
        assert await dirty_count(db) == 0
