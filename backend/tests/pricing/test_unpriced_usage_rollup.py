"""The unpriced-models list leaves usage rollups out of its token sums (spec §5.4).

Failure mode: an outer span repeating the usage of the unpriced call it wraps doubles the tokens
the price page shows for that model, while the call count is right.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.db.rls import bind_project
from app.pricing.queries import list_unpriced_models
from tests.jobs.bypass_support import SessionFactory
from tests.rollups.usage_rollup_support import ingest_rollup_pair


async def test_rollup_parent_excluded_from_tokens_but_counted(
    browser_factory: Any, client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    now = datetime.now(UTC)
    pair = await ingest_rollup_pair(
        browser_factory, client, now - timedelta(minutes=10), model="no-such-model"
    )

    async with session_factory() as db, db.begin():
        await bind_project(db, pair.project_id)
        [row] = await list_unpriced_models(
            db, pair.project_id, start=now - timedelta(hours=1), end=now
        )

    # The child's usage is the pair's billable usage (`ingest_rollup_pair`).
    assert (row.llm_calls, row.input_tokens, row.output_tokens) == (2, 300, 70)
