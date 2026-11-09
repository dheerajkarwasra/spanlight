"""Release figures leave usage rollups out of their token and cost sums (spec §5.4).

Failure mode: an agent span that reports the summed usage of the LLM calls under it was added
on top of them, so a release that nests calls showed about twice the Overview's tokens and cost,
and the release compare skewed by how much each release nests.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.api.window import TimeWindow
from app.db.rls import bind_project
from app.releases.service import list_releases
from tests.jobs.bypass_support import SessionFactory
from tests.rollups.usage_rollup_support import child_figures, ingest_rollup_pair


async def test_release_sums_leave_usage_rollups_out(
    browser_factory: Any, client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    start = datetime.now(UTC) - timedelta(minutes=10)
    pair = await ingest_rollup_pair(browser_factory, client, start)
    child = await child_figures(session_factory, pair)

    async with session_factory() as db, db.begin():
        await bind_project(db, pair.project_id)
        window = TimeWindow(start - timedelta(minutes=1), start + timedelta(minutes=1))
        (release,) = await list_releases(db, pair.project_id, window, None)

    assert (release.input_tokens, release.output_tokens) == (
        child.input_tokens,
        child.output_tokens,
    )
    assert release.cost_usd == child.cost_usd
    assert (release.llm_calls, release.unpriced_calls) == (2, 0)
