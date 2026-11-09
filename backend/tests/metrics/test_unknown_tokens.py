"""Token totals stay unknown when no call reported usage, on the raw and the rolled-up path.

Failure mode: an hour whose only call failed before the provider answered (no usage) reads
"0 tokens" on the overview, the models table and the chart, where the project rule is that an
unknown value is NULL, never 0. A call that did report keeps its partial sum.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.api.window import TimeWindow
from app.db.rls import bind_project
from app.metrics.queries import read_models, read_span_kpis, read_timeseries
from app.rollups.compute import floor_hour
from app.rollups.queries import read_model_rollups, read_rollups
from tests.helpers import bearer, create_workspace, new_trace_id, span
from tests.jobs.bypass_support import SessionFactory, fetch
from tests.rollups.error_support import compute


async def test_unreported_tokens_are_null_and_reported_ones_add(
    browser_factory: Any, client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    workspace = await create_workspace(await browser_factory("owner@example.com"))
    start = datetime.now(UTC) - timedelta(minutes=10)
    trace_id = new_trace_id()
    spans = [
        span(trace_id=trace_id, start=start, status="error", model="m-failed", usage=None),
        span(
            trace_id=trace_id,
            start=start,
            model="gpt-4o-mini",
            usage={"input_tokens": 300, "output_tokens": 70},
        ),
    ]
    response = await client.post(
        "/v1/traces", json={"spans": spans}, headers=bearer(workspace.api_key)
    )
    assert response.status_code == 200 and response.json()["rejected"] == [], response.text
    project_id = uuid.UUID(workspace.project_id)
    window = TimeWindow(start - timedelta(minutes=1), start + timedelta(minutes=1))

    async with session_factory() as db, db.begin():
        await bind_project(db, project_id)
        [kpis] = await read_span_kpis(db, project_id, [[window]], None)
        [point] = await read_timeseries(db, project_id, [window], None, "hour")
        models = {row.model: row for row in await read_models(db, project_id, [window], None)}

    assert kpis is not None and (kpis.input_tokens, kpis.output_tokens) == (300, 70)
    assert (point.input_tokens, point.output_tokens) == (300, 70)
    assert (models["m-failed"].input_tokens, models["m-failed"].output_tokens) == (None, None)
    assert (models["gpt-4o-mini"].input_tokens, models["gpt-4o-mini"].output_tokens) == (300, 70)

    hour = floor_hour(start)
    await compute(session_factory, project_id, hour, hour + timedelta(hours=1))
    stored = await fetch(
        session_factory,
        "SELECT model, input_tokens, output_tokens, cached_tokens FROM span_rollups_hourly "
        "WHERE project_id = :p ORDER BY model",
        {"p": project_id},
    )
    assert stored == [
        {"model": "gpt-4o-mini", "input_tokens": 300, "output_tokens": 70, "cached_tokens": None},
        {"model": "m-failed", "input_tokens": None, "output_tokens": None, "cached_tokens": None},
    ]

    rolled_window = TimeWindow(hour, hour + timedelta(hours=1))
    async with session_factory() as db, db.begin():
        await bind_project(db, project_id)
        [rolled] = (await read_rollups(db, project_id, rolled_window, None)).hours
        rolled_models = {
            row.model: row for row in await read_model_rollups(db, project_id, rolled_window, None)
        }
    assert (rolled.input_tokens, rolled.output_tokens, rolled.tokens) == (300, 70, 370)
    failed = rolled_models["m-failed"]
    assert (failed.input_tokens, failed.output_tokens) == (None, None)
