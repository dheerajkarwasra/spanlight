"""Public response metadata and alerts retain each output's actual dependencies."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest

from app.alerts.metrics import metric_series, metric_value
from app.alerts.types import Metric, MetricFilters
from app.api.window import TimeWindow
from app.db.rls import bind_project
from app.rollups.compute import compute_rollups, floor_hour
from tests.helpers import create_workspace
from tests.jobs.bypass_support import SessionFactory, execute, seed_llm_spans
from tests.rollups.recent_support import admit_at


@pytest.mark.parametrize("snapshot", [False, True])
async def test_object_and_list_headers_expose_scheduling_pending_and_unavailable(
    browser_factory: Any, session_factory: SessionFactory, snapshot: bool
) -> None:
    workspace = await create_workspace(await browser_factory("recent-status@example.com"))
    project = UUID(workspace.project_id)
    now = datetime.now(UTC)
    start = floor_hour(now) - timedelta(hours=50)
    await admit_at(session_factory, project, start)
    if snapshot:
        await seed_llm_spans(session_factory, project, 3, started_at=start)
    await seed_llm_spans(session_factory, project, 1, started_at=now)
    async with session_factory() as db:
        await compute_rollups(db, project, start, start + timedelta(hours=1))
        await compute_rollups(db, project, floor_hour(now), floor_hour(now) + timedelta(hours=1))
        await db.commit()
    params = {"from": start.isoformat(), "to": (start + timedelta(hours=1)).isoformat()}
    base = f"/api/v1/projects/{project}"
    overview = await workspace.owner.get(f"{base}/metrics/overview", params=params)
    assert overview.status_code == 200, overview.text
    assert overview.json()["current"]["aggregate_status"] == "pending"
    assert overview.json()["current"]["llm_calls"] == (3 if snapshot else 0)
    models = await workspace.owner.get(f"{base}/metrics/models", params=params)
    assert models.status_code == 200, models.text
    assert models.headers["Aggregate-Status"] == "pending"
    assert models.headers["Aggregate-Oldest-Pending-At"]
    if not snapshot:
        assert models.json() == []
    await execute(
        session_factory,
        "INSERT INTO aggregate_incomplete_hours(project_id,bucket_hour,reason) "
        "VALUES(:p,:h,'erasure')",
        {"p": project, "h": start},
    )
    response = await workspace.owner.get(f"{base}/metrics/overview", params=params)
    assert response.json()["current"]["aggregate_status"] == "unavailable"


async def test_disjoint_batches_raw_alert_and_source_loss(
    browser_factory: Any, session_factory: SessionFactory
) -> None:
    workspace = await create_workspace(await browser_factory("recent-alert@example.com"))
    project = UUID(workspace.project_id)
    now = datetime.now(UTC)
    start = floor_hour(now) - timedelta(hours=50)
    await admit_at(session_factory, project, start)
    await seed_llm_spans(session_factory, project, 3, started_at=now - timedelta(minutes=1))
    async with session_factory() as db:
        await compute_rollups(
            db, project, floor_hour(now) - timedelta(hours=1), floor_hour(now) + timedelta(hours=1)
        )
        await db.commit()
        await bind_project(db, project)
        windows = [
            TimeWindow(start - timedelta(hours=2), start),
            TimeWindow(start, start + timedelta(hours=1)),
            TimeWindow(now - timedelta(minutes=5), now),
        ]
        values = await metric_series(
            db, project, Metric.LLM_CALLS, windows, MetricFilters(), now=now
        )
        assert values[0].value == Decimal(0)
        assert values[1].value is None
        assert values[2].value == Decimal(3)
        single = await metric_value(
            db, project, Metric.LLM_CALLS, windows[2], MetricFilters(), now=now
        )
        assert single.value == Decimal(3)
    await execute(
        session_factory,
        "INSERT INTO aggregate_incomplete_hours(project_id,bucket_hour,reason) "
        "VALUES(:p,:h,'erasure')",
        {"p": project, "h": floor_hour(now - timedelta(minutes=1))},
    )
    async with session_factory() as db:
        await bind_project(db, project)
        single = await metric_value(
            db, project, Metric.LLM_CALLS, windows[2], MetricFilters(), now=now
        )
        assert single.value is None
