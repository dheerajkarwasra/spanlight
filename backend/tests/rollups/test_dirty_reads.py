"""Fresh source reads and pending materializations keep distinct dependencies."""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import text

from app.alerts.digest_queries import top_models
from app.alerts.metrics import metric_series, metric_value
from app.alerts.types import Metric, MetricFilters
from app.api.window import TimeWindow
from app.db.rls import bind_project
from app.rollups import dirty
from app.rollups.compute import compute_rollups
from app.rollups.coverage_reads import CoverageState, read_covered
from app.rollups.dirty import ALL, ATTRIBUTION, HOURLY, mark_dirty_hours
from tests.attribution.support import SessionFactory, SpanSeed, TraceSeed, seed_traces
from tests.helpers import create_workspace
from tests.rollups.dirty_support import dirty_row, dirty_rows


async def test_batched_alerts_only_gate_each_consumed_materialized_window(
    browser_factory: Any, session_factory: SessionFactory
) -> None:
    workspace = await create_workspace(await browser_factory("dirty-alerts@example.com"))
    project = uuid.UUID(workspace.project_id)
    now = datetime.now(UTC).replace(hour=12, minute=34, second=0, microsecond=0)
    old = now.replace(hour=8, minute=0)
    live = now - timedelta(minutes=4)
    windows = [
        TimeWindow(old, old + timedelta(hours=1)),
        TimeWindow(now - timedelta(minutes=5), now),
    ]
    async with session_factory() as db:
        await bind_project(db, project)
        await seed_traces(
            db,
            project,
            {
                "old": TraceSeed(old, (SpanSeed("llm", old, Decimal(1), 10, 2),)),
                "live": TraceSeed(live, (SpanSeed("llm", live, Decimal(2), 10, 2),)),
            },
        )
        await compute_rollups(db, project, old, old + timedelta(hours=1))
        await mark_dirty_hours(db, project, [old, live])
        values = await metric_series(
            db, project, Metric.LLM_CALLS, windows, MetricFilters(), now=now
        )
        standalone = await metric_value(
            db, project, Metric.LLM_CALLS, windows[1], MetricFilters(), now=now
        )
        assert values[0].value is None
        assert values[1].value == standalone.value == Decimal(1)
        assert await top_models(db, project, windows[0]) == []


async def test_pending_materialized_list_metadata_survives_empty_result(
    browser_factory: Any, session_factory: SessionFactory
) -> None:
    workspace = await create_workspace(await browser_factory("dirty-list@example.com"))
    project = uuid.UUID(workspace.project_id)
    hour = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) - timedelta(days=5)
    async with session_factory() as db:
        await bind_project(db, project)
        await mark_dirty_hours(db, project, [hour])
        await db.commit()
    response = await workspace.owner.get(
        f"/api/v1/projects/{project}/providers/errors/timeseries",
        params={"from": hour.isoformat(), "to": (hour + timedelta(hours=1)).isoformat()},
    )
    assert response.status_code == 200, response.text
    assert response.headers["Aggregate-Status"] == "pending"
    assert response.headers["Aggregate-Oldest-Pending-At"]


async def test_disabled_optional_obligations_do_not_suppress_hourly_digest(
    browser_factory: Any, session_factory: SessionFactory
) -> None:
    workspace = await create_workspace(await browser_factory("dirty-optional@example.com"))
    project = uuid.UUID(workspace.project_id)
    hour = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) - timedelta(hours=5)
    window = TimeWindow(hour, hour + timedelta(hours=1))
    async with session_factory() as db:
        await bind_project(db, project)
        await seed_traces(
            db, project, {"old": TraceSeed(hour, (SpanSeed("llm", hour, Decimal(1), 10, 2),))}
        )
        await compute_rollups(db, project, hour, window.end)
        # This row represents retained optional work; no source correction is hidden by a fixture.
        await db.execute(
            text(
                "INSERT INTO aggregate_dirty_hours "
                "(project_id,bucket_hour,generation,pending_consumers,first_marked_at) VALUES "
                "(:p,:h,nextval('aggregate_dirty_generation_seq'),:mask,:age)"
            ),
            {"p": project, "h": hour, "mask": ATTRIBUTION, "age": hour - timedelta(days=2)},
        )
        retained = await dirty_row(db)
        assert await top_models(db, project, window)
        await mark_dirty_hours(db, project, [hour])
        rows = await dirty_rows(db)
        if dirty.WRITE_SHARDS:
            optional, marked = rows
            assert optional == retained
            assert 0 <= marked.shard < dirty.DIRTY_SHARDS
            assert marked.pending_consumers == ALL
            assert marked.first_marked_at > retained.first_marked_at
        else:
            (marked,) = rows
            assert marked.shard == -1
            assert marked.pending_consumers == ALL
            assert marked.first_marked_at == retained.first_marked_at
        assert marked.generation != retained.generation
        assert await top_models(db, project, window) == []
        coverage = CoverageState()
        await read_covered(
            db,
            text("SELECT 1"),
            {},
            project,
            window.start,
            window.end,
            coverage,
            relevant_consumers=HOURLY,
        )
        assert coverage.metadata() == {
            "aggregate_status": "pending",
            "oldest_pending_at": marked.first_marked_at,
        }


async def test_attribution_raw_window_is_fresh_while_daily_products_remain_pending(
    browser_factory: Any, session_factory: SessionFactory
) -> None:
    workspace = await create_workspace(await browser_factory("dirty-products@example.com"))
    project = uuid.UUID(workspace.project_id)
    hour = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) - timedelta(hours=5)
    async with session_factory() as db:
        await bind_project(db, project)
        await mark_dirty_hours(db, project, [hour])
        await db.commit()
    base = f"/api/v1/projects/{project}"
    short = {"from": hour.isoformat(), "to": (hour + timedelta(hours=1)).isoformat()}
    raw = await workspace.owner.get(
        f"{base}/attribution", params={**short, "group_by": "trace_name"}
    )
    assert raw.status_code == 200, raw.text
    assert raw.json()["aggregate_status"] == "complete"
    assert raw.json()["oldest_pending_at"] is None
    daily = await workspace.owner.get(
        f"{base}/attribution",
        params={
            "from": (hour - timedelta(days=2)).isoformat(),
            "to": short["to"],
            "group_by": "trace_name",
        },
    )
    assert daily.status_code == 200, daily.text
    assert daily.json()["aggregate_status"] == "pending"
    assert daily.json()["oldest_pending_at"] is not None
    users = await workspace.owner.get(f"{base}/users", params=short)
    assert users.status_code == 200, users.text
    assert users.json()["aggregate_status"] == "pending"
    mix = await workspace.owner.get(f"{base}/metrics/token-mix", params=short)
    assert mix.status_code == 200, mix.text
    assert mix.json()["aggregate_status"] == "pending"
