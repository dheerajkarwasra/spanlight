"""Public reads cannot turn erased source rows into fresh zero values."""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from app.alerts.metrics import metric_value
from app.alerts.types import Metric, MetricFilters
from app.api.window import TimeWindow
from app.db.rls import bypass_rls
from app.end_users.erasure_job import erase_end_user
from app.rollups.compute import compute_rollups
from tests.attribution.support import SessionFactory, SpanSeed, TraceSeed, seed_traces
from tests.helpers import create_workspace


@pytest.mark.parametrize("snapshot", [False, True])
async def test_metrics_reads_expose_unavailable_and_preserve_snapshot(
    browser_factory: Any,
    session_factory: SessionFactory,
    snapshot: bool,
) -> None:
    workspace = await create_workspace(await browser_factory("coverage@example.com"))
    project = uuid.UUID(workspace.project_id)
    hour = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) - timedelta(hours=2)
    async with session_factory() as db:
        await bypass_rls(db)
        await seed_traces(
            db,
            project,
            {
                "erased": TraceSeed(
                    hour,
                    (SpanSeed("llm", hour, Decimal("1.00"), 100, 20),),
                    external_user_id="erased",
                )
            },
        )
        if snapshot:
            await compute_rollups(db, project, hour, hour + timedelta(hours=1))
        await db.commit()
    await erase_end_user(session_factory, project_id=project, external_user_id="erased", counts={})
    params = {"from": hour.isoformat(), "to": (hour + timedelta(hours=1)).isoformat()}
    base = f"/api/v1/projects/{project}/metrics"
    overview = await workspace.owner.get(f"{base}/overview", params=params)
    assert overview.status_code == 200, overview.text
    current = overview.json()["current"]
    assert current["aggregate_status"] == "unavailable"
    assert current["llm_calls"] == (1 if snapshot else None)
    assert current["cost_usd"] == ("1.00000000" if snapshot else None)
    models = await workspace.owner.get(f"{base}/models", params=params)
    assert models.status_code == 200, models.text
    assert models.headers["Aggregate-Status"] == "unavailable"
    assert isinstance(models.json(), list)
    if not snapshot:
        assert models.json() == []
    series = await workspace.owner.get(f"{base}/timeseries", params=params)
    assert series.headers["Aggregate-Status"] == "unavailable"
    assert series.json()[0]["llm_calls"] == (1 if snapshot else None)
    health = await workspace.owner.get(
        f"/api/v1/projects/{project}/providers/health", params=params
    )
    assert health.json()["aggregate_status"] == "unavailable"
    errors = await workspace.owner.get(
        f"/api/v1/projects/{project}/providers/errors/timeseries", params=params
    )
    assert errors.headers["Aggregate-Status"] == "unavailable"


async def test_daily_and_raw_attribution_reads_do_not_invent_missing_values(
    browser_factory: Any,
    session_factory: SessionFactory,
) -> None:
    workspace = await create_workspace(await browser_factory("daily-coverage@example.com"))
    project = uuid.UUID(workspace.project_id)
    hour = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) - timedelta(hours=2)
    async with session_factory() as db:
        await bypass_rls(db)
        await seed_traces(
            db,
            project,
            {
                "erased": TraceSeed(
                    hour,
                    (SpanSeed("llm", hour, Decimal("1.00"), 100, 20),),
                    external_user_id="erased",
                    name="answer",
                )
            },
        )
        await db.commit()
    await erase_end_user(session_factory, project_id=project, external_user_id="erased", counts={})
    params = {"from": hour.isoformat(), "to": (hour + timedelta(hours=1)).isoformat()}
    base = f"/api/v1/projects/{project}"
    attribution = await workspace.owner.get(
        f"{base}/attribution", params={**params, "group_by": "trace_name"}
    )
    assert attribution.status_code == 200, attribution.text
    assert attribution.json()["aggregate_status"] == "unavailable"
    assert attribution.json()["totals"]["llm_calls"] is None
    series = await workspace.owner.get(
        f"{base}/attribution/series", params={**params, "group_by": "trace_name"}
    )
    assert series.status_code == 200, series.text
    assert series.json()["aggregate_status"] == "unavailable"
    assert series.json()["points"][0]["cost_usd"] is None
    users = await workspace.owner.get(f"{base}/users", params=params)
    assert users.json()["aggregate_status"] == "unavailable"
    assert users.json()["items"] == []
    mix = await workspace.owner.get(f"{base}/metrics/token-mix", params=params)
    assert mix.json()["aggregate_status"] == "unavailable"
    assert mix.json()["uncached_input_tokens"] is None


async def test_alert_observations_are_unknown_after_source_loss(
    browser_factory: Any,
    session_factory: SessionFactory,
) -> None:
    workspace = await create_workspace(await browser_factory("alert-coverage@example.com"))
    project = uuid.UUID(workspace.project_id)
    hour = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) - timedelta(hours=2)
    async with session_factory() as db:
        await bypass_rls(db)
        await seed_traces(
            db,
            project,
            {
                "erased": TraceSeed(
                    hour,
                    (SpanSeed("llm", hour, Decimal("1.00"), 100, 20),),
                    external_user_id="erased",
                )
            },
        )
        await compute_rollups(db, project, hour, hour + timedelta(hours=1))
        await db.commit()
    await erase_end_user(session_factory, project_id=project, external_user_id="erased", counts={})
    async with session_factory() as db:
        await bypass_rls(db)
        observation = await metric_value(
            db,
            project,
            Metric.LLM_CALLS,
            TimeWindow(hour, hour + timedelta(hours=1)),
            MetricFilters(),
        )
    assert observation.value is None
