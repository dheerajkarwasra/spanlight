"""An hour of $1,000,000 or more keeps its exact cost in the rollups and in every sum of them.

`span_rollups_hourly.cost_usd` is unbounded `numeric`, so the $1M guard of the `numeric(14, 8)`
columns (trace totals, attribution) does not apply. Failure mode: a capped hour stored as NULL is
skipped by every reader that adds hours, so the overview, cost alerts and budget spend read a
lower total than the raw spans, and a budget above $1M never fires.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from app.alerts.metrics import metric_value
from app.alerts.types import Metric, MetricFilters
from app.api.window import TimeWindow
from app.db.rls import bind_project
from app.metrics.service import overview
from app.rollups.compute import compute_rollups
from tests.attribution.support import SessionFactory, SpanSeed, TraceSeed, seed_traces
from tests.helpers import create_workspace
from tests.jobs.bypass_support import fetch

_HALF = Decimal("600000")
_TOTAL = Decimal("1200001")


async def _seeded(
    browser_factory: Any, session_factory: SessionFactory, email: str
) -> tuple[uuid.UUID, datetime]:
    """Two $600,000 calls in one old hour (one rollup row of $1.2M) and a $1 call the hour after."""
    workspace = await create_workspace(await browser_factory(email))
    project = uuid.UUID(workspace.project_id)
    hour = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) - timedelta(days=2)
    later = hour + timedelta(hours=1)
    async with session_factory() as db:
        await bind_project(db, project)
        await seed_traces(
            db,
            project,
            {
                "a": TraceSeed(hour, (SpanSeed("llm", hour, _HALF, 10, 2),)),
                "b": TraceSeed(hour, (SpanSeed("llm", hour, _HALF, 10, 2),)),
                "c": TraceSeed(later, (SpanSeed("llm", later, Decimal(1), 10, 2),)),
            },
        )
        await compute_rollups(db, project, hour, later + timedelta(hours=1))
        await db.commit()
    return project, hour


async def test_rollup_row_keeps_a_cost_above_a_million(
    browser_factory: Any, session_factory: SessionFactory
) -> None:
    project, hour = await _seeded(browser_factory, session_factory, "bound-row@example.com")
    rows = await fetch(
        session_factory,
        "SELECT cost_usd FROM span_rollups_hourly WHERE project_id = :p AND bucket_start = :h",
        {"p": project, "h": hour},
    )
    assert rows == [{"cost_usd": 2 * _HALF}]


async def test_spend_metric_counts_an_hour_above_a_million(
    browser_factory: Any, session_factory: SessionFactory
) -> None:
    """The `spend` metric is what budgets and cost alert rules evaluate."""
    project, hour = await _seeded(browser_factory, session_factory, "bound-spend@example.com")
    window = TimeWindow(hour, hour + timedelta(hours=2))
    async with session_factory() as db:
        await bind_project(db, project)
        value = await metric_value(
            db, project, Metric.SPEND, window, MetricFilters(), now=hour + timedelta(days=1)
        )
    assert value.value == _TOTAL


async def test_overview_cost_counts_an_hour_above_a_million(
    browser_factory: Any, session_factory: SessionFactory
) -> None:
    project, hour = await _seeded(browser_factory, session_factory, "bound-overview@example.com")
    window = TimeWindow(hour, hour + timedelta(hours=2))
    async with session_factory() as db:
        await bind_project(db, project)
        result = await overview(db, project, window, None)
    assert result.approximate, "the window must be read from the rollups"
    assert result.current.cost_usd == _TOTAL
