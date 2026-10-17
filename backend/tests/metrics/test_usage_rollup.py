"""The raw-span metrics (windows of 24 h or less) leave usage rollups out of their sums
(spec §5.4), as the hourly rollups behind longer windows do.

Failure mode: the dashboard shows one cost on "last 24 h" and half of it on "last 7 d" because
an outer span repeating the usage of the call it wraps is summed on the raw path only.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.api.window import TimeWindow
from app.db.rls import bind_project
from app.metrics.queries import read_models, read_span_kpis, read_timeseries
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
        kpis = await read_span_kpis(db, pair.project_id, window, None)
        points = await read_timeseries(db, pair.project_id, window, None, "hour")
        [model] = await read_models(db, pair.project_id, window, None)

    assert (kpis.llm_calls, kpis.llm_errors, kpis.unpriced_calls) == (2, 1, 0)
    assert (kpis.input_tokens, kpis.output_tokens) == (child.input_tokens, child.output_tokens)
    assert kpis.cost_usd == child.cost_usd

    assert sum(p.llm_calls for p in points) == 2 and sum(p.errors for p in points) == 1
    assert sum(p.tokens for p in points) == child.input_tokens + child.output_tokens
    assert sum(p.cost_usd for p in points if p.cost_usd is not None) == child.cost_usd

    assert (model.calls, model.errors) == (2, 1)
    assert (model.input_tokens, model.output_tokens) == (child.input_tokens, child.output_tokens)
    assert model.cost_usd == child.cost_usd
