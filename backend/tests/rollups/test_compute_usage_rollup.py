"""The hourly span rollups leave usage rollups out of their sums (spec §5.4).

Failure mode: a parent span repeating its child's usage doubles the hour's tokens and cost on
every chart and alert read from `span_rollups_hourly`, while its span and error counts are right.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.rollups.compute import floor_hour
from tests.jobs.bypass_support import SessionFactory, fetch
from tests.rollups.error_support import compute
from tests.rollups.usage_rollup_support import child_figures, ingest_rollup_pair

_ROWS = """
    SELECT span_count, errors, input_tokens, output_tokens, cost_usd, unpriced_calls
    FROM span_rollups_hourly WHERE project_id = :p
"""


async def test_rollup_parent_excluded_from_sums_but_counted(
    browser_factory: Any, client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    start = datetime.now(UTC) - timedelta(minutes=10)
    pair = await ingest_rollup_pair(browser_factory, client, start)
    child = await child_figures(session_factory, pair)

    hour = floor_hour(start)
    await compute(session_factory, pair.project_id, hour, hour + timedelta(hours=1))

    [row] = await fetch(session_factory, _ROWS, {"p": pair.project_id})
    assert row == {
        "span_count": 2,
        "errors": 1,
        "input_tokens": child.input_tokens,
        "output_tokens": child.output_tokens,
        "cost_usd": child.cost_usd,
        "unpriced_calls": 0,
    }
