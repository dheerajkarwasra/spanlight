"""The detectors' span read leaves usage rollups out (spec §5.4).

Failure mode: the Doctor's retry, cost-spike and unpriced-spend detectors see an outer span that
repeats the usage of the call it wraps as a second, equally priced call.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.insights.context_queries import load_context
from tests.jobs.bypass_support import SessionFactory
from tests.rollups.usage_rollup_support import child_figures, ingest_rollup_pair


async def test_rollup_parent_left_out_of_the_llm_spans(
    browser_factory: Any, client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    pair = await ingest_rollup_pair(browser_factory, client, now - timedelta(minutes=10))
    await child_figures(session_factory, pair)

    async with session_factory() as db:
        ctx = await load_context(db, pair.project_id, now)
        await db.commit()

    assert ctx is not None
    assert [row.span_id for row in ctx.llm_spans] == [pair.child_span_id]
