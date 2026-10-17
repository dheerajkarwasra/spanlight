"""A prompt version's usage leaves usage rollups out of its cost (spec §5.4).

Failure mode: the prompt page bills a version twice for one call because an outer span repeats
the usage of the call it wraps, while the call count is right.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.db.rls import bind_project
from app.prompts.usage import version_usage
from tests.jobs.bypass_support import SessionFactory
from tests.rollups.usage_rollup_support import child_figures, ingest_rollup_pair


async def test_rollup_parent_excluded_from_cost_but_counted(
    browser_factory: Any, client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    now = datetime.now(UTC)
    version_id = uuid.uuid4()
    pair = await ingest_rollup_pair(
        browser_factory, client, now - timedelta(minutes=10), prompt_version_id=str(version_id)
    )
    child = await child_figures(session_factory, pair)

    async with session_factory() as db, db.begin():
        await bind_project(db, pair.project_id)
        usage = await version_usage(db, pair.project_id, version_id, now)

    assert (usage.traces, usage.llm_calls) == (1, 2)
    assert (usage.cost_usd, usage.has_unpriced) == (child.cost_usd, False)
