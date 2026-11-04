"""Dependency ranges, relevant ages and source loss compose without false freshness."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text

from app.alerts.metrics import metric_series
from app.alerts.types import Metric, MetricFilters
from app.api.window import TimeWindow
from app.db.rls import bind_project
from app.providers.queries import read_rollup_calls
from app.rollups.coverage_reads import CoverageState, read_covered
from app.rollups.dirty import ATTRIBUTION, COST_DAILY, HOURLY, USER_DAILY, mark_dirty_hours
from tests.attribution.support import SessionFactory
from tests.helpers import create_workspace


async def test_relevant_minimum_accumulates_and_raw_never_resets_pending(
    browser_factory: Any, session_factory: SessionFactory
) -> None:
    workspace = await create_workspace(await browser_factory("dirty-range@example.com"))
    project = uuid.UUID(workspace.project_id)
    hour = datetime(2026, 10, 20, 10, tzinfo=UTC)
    end = hour + timedelta(hours=1)
    old_age, hourly_age, daily_age = (
        hour - timedelta(days=3),
        hour - timedelta(days=1),
        hour - timedelta(days=2),
    )
    async with session_factory() as db:
        await bind_project(db, project)
        await db.execute(
            text("""INSERT INTO aggregate_dirty_hours
            (project_id,bucket_hour,generation,pending_consumers,first_marked_at)
            VALUES (:p,:h,nextval('aggregate_dirty_generation_seq'),:mask,:age)"""),
            [
                {"p": project, "h": hour, "mask": HOURLY, "age": hourly_age},
                {
                    "p": project,
                    "h": hour + timedelta(hours=1),
                    "mask": ATTRIBUTION | USER_DAILY,
                    "age": old_age,
                },
                {
                    "p": project,
                    "h": hour + timedelta(hours=2),
                    "mask": COST_DAILY,
                    "age": daily_age,
                },
            ],
        )
        state = CoverageState()
        assert await read_rollup_calls(db, project, (hour, end), None, state) == []
        assert state.status == "pending" and state.oldest_pending_at == hourly_age
        assert (
            await read_covered(
                db,
                text("SELECT 1 WHERE false"),
                {},
                project,
                hour,
                end,
                state,
                relevant_consumers=0,
                raw=True,
            )
            == []
        )
        assert state.status == "pending" and state.oldest_pending_at == hourly_age
        await read_covered(
            db,
            text("SELECT 1"),
            {},
            project,
            hour.replace(hour=0),
            hour.replace(hour=0) + timedelta(days=1),
            state,
            relevant_consumers=COST_DAILY,
        )
        assert state.oldest_pending_at == daily_age
        optional = CoverageState()
        await read_covered(
            db,
            text("SELECT 1"),
            {},
            project,
            hour,
            end + timedelta(hours=1),
            optional,
            relevant_consumers=USER_DAILY,
        )
        assert optional.status == "pending" and optional.oldest_pending_at == old_age
        await db.execute(
            text(
                "INSERT INTO aggregate_incomplete_hours (project_id,bucket_hour,reason) "
                "VALUES (:p,:h,'retention')"
            ),
            {"p": project, "h": hour},
        )
        assert (
            await read_covered(
                db, text("SELECT 1"), {}, project, hour, end, state, relevant_consumers=0, raw=True
            )
            == []
        )
        assert state.status == "unavailable" and state.oldest_pending_at == daily_age


async def test_unused_gap_and_dirty_live_tail_do_not_contaminate_alert_windows(
    browser_factory: Any, session_factory: SessionFactory
) -> None:
    workspace = await create_workspace(await browser_factory("dirty-gap@example.com"))
    project = uuid.UUID(workspace.project_id)
    now = datetime(2026, 10, 20, 12, 34, tzinfo=UTC)
    async with session_factory() as db:
        await bind_project(db, project)
        await mark_dirty_hours(db, project, [now.replace(hour=8), now])
        windows = [
            TimeWindow(now.replace(hour=7, minute=0), now.replace(hour=8, minute=0)),
            TimeWindow(now.replace(hour=9, minute=0), now),
        ]
        values = await metric_series(
            db, project, Metric.LLM_CALLS, windows, MetricFilters(), now=now
        )
        assert values[0].value == values[1].value == 0
        await mark_dirty_hours(db, project, [now.replace(hour=9)])
        values = await metric_series(
            db, project, Metric.LLM_CALLS, windows, MetricFilters(), now=now
        )
        assert values[0].value == 0 and values[1].value is None
        forced = await metric_series(
            db, project, Metric.LLM_CALLS, windows, MetricFilters(external_user_id="alice"), now=now
        )
        assert forced[0].value == forced[1].value == 0


@pytest.mark.parametrize("mask", [-1, 16, True, 1.5])
async def test_invalid_read_mask_is_rejected_before_sql(
    session_factory: SessionFactory, mask: Any
) -> None:
    async with session_factory() as db:
        with pytest.raises(ValueError, match="integer mask"):
            await read_covered(
                db,
                text("SELECT 1"),
                {},
                uuid.uuid4(),
                datetime.now(UTC),
                datetime.now(UTC),
                relevant_consumers=mask,
            )
