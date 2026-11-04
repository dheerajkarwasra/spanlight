"""Public mixed-source percentiles track an independent PostgreSQL oracle."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import httpx
import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.alerts.metrics import metric_value
from app.alerts.types import Metric, MetricFilters
from app.api.window import TimeWindow
from app.config import Settings
from app.db.rls import bind_project
from app.metrics import service
from app.rollups.compute import compute_rollups, floor_hour
from app.rollups.dirty import mark_dirty_hours
from tests.helpers import Workspace, bearer, create_workspace, new_trace_id, span
from tests.jobs.bypass_support import SessionFactory
from tests.metrics import legacy_overview
from tests.rollups.recent_support import run_recent
from tests.rollups.test_dirty_repair import repair


@dataclass
class PercentileCase:
    workspace: Workspace
    foreign: Workspace
    now: datetime
    old_hour: datetime
    p95: float
    ttft: float
    cost: Decimal

    @property
    def project(self) -> UUID:
        return UUID(self.workspace.project_id)

    @property
    def base(self) -> str:
        return f"/api/v1/projects/{self.project}/metrics"

    @property
    def params(self) -> dict[str, str]:
        return {"from": self.old_hour.isoformat(), "to": self.now.isoformat()}


async def _ingest_populations(
    client: httpx.AsyncClient,
    workspace: Workspace,
    foreign: Workspace,
    now: datetime,
    old_hour: datetime,
) -> None:
    payload = [
        span(
            trace_id=new_trace_id(),
            start=old_hour + timedelta(minutes=5) if index < 9 else now - timedelta(minutes=5),
            duration_ms=2 if index < 19 else 2000,
            time_to_first_token_ms=2 if index < 19 else 2000,
        )
        for index in range(20)
    ]
    ingested = await client.post(
        "/v1/traces", json={"spans": payload}, headers=bearer(workspace.api_key)
    )
    assert ingested.status_code == 200
    assert ingested.json() == {"accepted": 20, "rejected": []}
    other = await client.post(
        "/v1/traces",
        json={"spans": [span(trace_id=new_trace_id(), duration_ms=100_000)]},
        headers=bearer(foreign.api_key),
    )
    assert other.status_code == 200


@pytest.fixture
async def percentile_case(
    browser_factory: Any, client: httpx.AsyncClient, session_factory: SessionFactory
) -> PercentileCase:
    workspace = await create_workspace(await browser_factory("percentile-owner@example.com"))
    foreign = await create_workspace(await browser_factory("percentile-foreign@example.com"))
    project = UUID(workspace.project_id)
    now = datetime.now(UTC)
    old_hour = floor_hour(now) - timedelta(hours=3)
    await _ingest_populations(client, workspace, foreign, now, old_hour)
    async with session_factory() as db:
        await compute_rollups(db, project, old_hour, old_hour + timedelta(hours=1))
        await compute_rollups(db, project, floor_hour(now), floor_hour(now) + timedelta(hours=1))
        await db.commit()
        await bind_project(db, project)
        oracle = (
            await db.execute(
                text(
                    "SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY duration_ms) AS p50, "
                    "percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms) AS p95, "
                    "percentile_cont(0.95) WITHIN GROUP (ORDER BY time_to_first_token_ms) AS ttft, "
                    "sum(cost_usd) AS cost FROM spans"
                )
            )
        ).one()
        assert oracle.p50 == 2.0
        assert oracle.p95 == pytest.approx(101.9)
        assert oracle.ttft == pytest.approx(101.9)
    return PercentileCase(workspace, foreign, now, old_hour, oracle.p95, oracle.ttft, oracle.cost)


async def test_mixed_metrics_preserve_percentiles_and_exact_totals(
    percentile_case: PercentileCase,
) -> None:
    case = percentile_case
    response = await case.workspace.owner.get(f"{case.base}/overview", params=case.params)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["approximate"] is True
    current = result["current"]
    assert current["llm_calls"] == 20
    assert current["traces"] == 20
    assert current["input_tokens"] == 20_000
    assert current["output_tokens"] == 10_000
    assert Decimal(current["cost_usd"]) == case.cost
    assert current["p50_ms"] == pytest.approx(2.0, rel=0.201)
    assert current["p95_ms"] == pytest.approx(case.p95, rel=0.201)
    models = await case.workspace.owner.get(f"{case.base}/models", params=case.params)
    assert models.status_code == 200
    [model] = models.json()
    assert model["approximate"] is True
    assert model["calls"] == 20
    assert model["p95_ms"] == pytest.approx(case.p95, rel=0.201)


async def test_raw_only_percentile_stays_exact(percentile_case: PercentileCase) -> None:
    case = percentile_case
    response = await case.workspace.owner.get(
        f"{case.base}/overview",
        params={"from": (case.now - timedelta(minutes=10)).isoformat(), "to": case.now.isoformat()},
    )
    assert response.status_code == 200
    assert response.json()["approximate"] is False
    assert response.json()["current"]["llm_calls"] == 11
    assert response.json()["current"]["p95_ms"] == pytest.approx(1001.0)


async def test_alerts_match_the_oracle_and_foreign_metrics_stay_hidden(
    percentile_case: PercentileCase, session_factory: SessionFactory, settings: Settings
) -> None:
    case = percentile_case
    # Alerts intentionally stay unknown while ingested history has pending repair/closure.
    await repair(session_factory, settings)
    await run_recent(session_factory, settings, case.now)
    async with session_factory() as db:
        await bind_project(db, case.project)
        window = TimeWindow(case.old_hour, case.now)
        latency = await metric_value(
            db, case.project, Metric.P95_MS, window, MetricFilters(), now=case.now
        )
        ttft = await metric_value(
            db, case.project, Metric.TTFT_P95_MS, window, MetricFilters(), now=case.now
        )
    assert latency.approximate is True
    assert float(latency.value) == pytest.approx(case.p95, rel=0.201)
    assert ttft.approximate is True
    assert float(ttft.value) == pytest.approx(case.ttft, rel=0.201)
    denied = await case.workspace.owner.get(
        f"/api/v1/projects/{case.foreign.project_id}/metrics/overview", params=case.params
    )
    assert denied.status_code == 404


def _windows(end: datetime) -> list[TimeWindow]:
    lengths = (timedelta(hours=1), timedelta(hours=24), timedelta(days=7), timedelta(days=30))
    return [
        TimeWindow(stop - length, stop)
        for stop in (end, floor_hour(end), floor_hour(end) - timedelta(minutes=20))
        for length in lengths
    ]


async def test_overview_reads_both_periods_in_one_statement_per_family(
    percentile_case: PercentileCase, session_factory: SessionFactory, engine: AsyncEngine
) -> None:
    case = percentile_case
    statements: list[str] = []

    def record(_conn: Any, _cursor: Any, statement: str, *_rest: Any) -> None:
        statements.append(statement)

    async with session_factory() as db:
        await bind_project(db, case.project)
        event.listen(engine.sync_engine, "before_cursor_execute", record)
        try:
            await service.overview(db, case.project, TimeWindow(case.old_hour, case.now), None)
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", record)
    # The split, the span and trace rollup hours, the raw span KPIs and the raw trace counts.
    assert len(statements) == 5, "\n\n".join(statements)


async def test_one_pass_overview_equals_the_frozen_two_pass_path(
    percentile_case: PercentileCase,
    session_factory: SessionFactory,
    browser_factory: Any,
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = percentile_case
    hour = floor_hour(case.now)
    # Both periods populated, spans on hour boundaries, unknown costs, two environments.
    extra = [
        span(
            trace_id=new_trace_id(),
            start=at,
            duration_ms=ms,
            model=model,
            trace={"name": "answer", "environment": env},
        )
        for at, ms, model, env in [
            (hour - timedelta(hours=1), 40, "gpt-4o-mini", "prod"),
            (hour - timedelta(minutes=61), 900, "unknown-model-x", "staging"),
            (hour - timedelta(hours=24), 55, "gpt-4o-mini", "prod"),
            (case.now - timedelta(hours=24, minutes=30), 300, "gpt-4o-mini", "staging"),
            (case.now - timedelta(hours=25), 7, "unknown-model-x", "prod"),
            (case.now - timedelta(hours=30), 70, "gpt-4o-mini", "staging"),
            (case.now - timedelta(days=6, hours=3), 120, "gpt-4o-mini", "staging"),
            (case.now - timedelta(days=8), 5000, "unknown-model-x", "prod"),
            (case.now - timedelta(days=20), 60, "gpt-4o-mini", "prod"),
        ]
    ]
    ingested = await client.post(
        "/v1/traces", json={"spans": extra}, headers=bearer(case.workspace.api_key)
    )
    assert ingested.status_code == 200, ingested.text
    async with session_factory() as db:
        await compute_rollups(db, case.project, hour - timedelta(days=9), hour)
        await db.commit()
    empty = UUID((await create_workspace(await browser_factory("empty@example.com"))).project_id)
    windows = [
        *_windows(case.now),
        TimeWindow(case.old_hour, case.old_hour + timedelta(hours=1)),
        TimeWindow(case.old_hour + timedelta(hours=1), case.old_hour + timedelta(hours=2)),
        TimeWindow(case.old_hour, case.now),
    ]
    splits = [None, hour - timedelta(hours=1), hour - timedelta(hours=25), hour - timedelta(days=3)]

    async def compare(project: UUID, splits: list[datetime | None] = splits) -> None:
        for split in splits:

            async def fixed(*_args: Any, split: datetime | None = split) -> datetime | None:
                return split

            monkeypatch.setattr(service, "read_split", fixed)
            for environment in (None, "prod", "staging"):
                for window in windows:
                    new = await service.overview(db, project, window, environment)
                    old = await legacy_overview.overview(db, project, window, environment, split)
                    assert new == old, (split, environment, window)

    async with session_factory() as db:
        # One snapshot for both paths.
        await db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
        await bind_project(db, empty)
        await compare(empty)
        nulls = await service.overview(db, empty, TimeWindow(case.old_hour, case.now), None)
        assert nulls.current.cost_usd is None and nulls.current.p95_ms is None
        assert nulls.previous.p50_ms is None and nulls.current.error_rate is None
        await bind_project(db, case.project)
        await compare(case.project)
        # Independent states: an incomplete hour in one period, a pending hour in another.
        await db.execute(
            text(
                "INSERT INTO aggregate_incomplete_hours (project_id, bucket_hour, reason) "
                "VALUES (:p, :h, 'retention')"
            ),
            {"p": case.project, "h": case.old_hour},
        )
        await mark_dirty_hours(db, case.project, [hour - timedelta(hours=30)])
        await compare(case.project)
        parts = await service.overview(db, case.project, windows[-2], None)
        assert parts.previous.aggregate_status == "unavailable"
        assert parts.current.aggregate_status != "unavailable"
        # The last split compared (three days back) leaves the last day raw, where pending
        # rollup work does not apply; at the newest split the previous day reads rollups.
        await compare(case.project, [hour - timedelta(hours=1)])
        day = await service.overview(
            db, case.project, TimeWindow(hour - timedelta(hours=24), hour), None
        )
        assert day.current.aggregate_status == "unavailable"
        assert day.previous.aggregate_status == "pending"
