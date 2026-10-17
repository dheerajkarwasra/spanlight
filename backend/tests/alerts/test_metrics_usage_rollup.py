"""The raw-span alert aggregates leave usage rollups out of their sums (spec §5.4).

Failure mode: a budget (metric `spend`) or a token alert counts a call twice because an outer
span repeats the usage of the call it wraps, while the call and error counts are right.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.alerts.metrics_queries import read_window_aggregates
from app.alerts.types import MetricFilters
from app.api.window import TimeWindow
from app.db.rls import bind_project
from tests.jobs.bypass_support import SessionFactory
from tests.rollups.usage_rollup_support import child_figures, ingest_rollup_pair


async def test_rollup_parent_excluded_from_sums_but_counted(
    browser_factory: Any, client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    start = datetime.now(UTC) - timedelta(minutes=10)
    pair = await ingest_rollup_pair(browser_factory, client, start)
    child = await child_figures(session_factory, pair)
    window = TimeWindow(start - timedelta(minutes=1), start + timedelta(minutes=1))

    async with session_factory() as db, db.begin():
        await bind_project(db, pair.project_id)
        [row] = await read_window_aggregates(db, pair.project_id, [[window]], MetricFilters())

    assert (row.spans, row.llm_calls, row.llm_errors) == (2, 2, 1)
    assert (row.input_tokens, row.output_tokens) == (child.input_tokens, child.output_tokens)
    assert row.cost_usd == child.cost_usd
