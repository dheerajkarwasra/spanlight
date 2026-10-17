"""The per-user daily stats leave usage rollups out of their sums (spec §5.4).

Failure mode: the Users page bills a user twice for one call because an outer span repeats the
usage of the call it wraps, while the call and error counts are right.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.db.rls import bind_project
from app.end_users.refresh import refresh_user_stats
from tests.jobs.bypass_support import SessionFactory, fetch
from tests.rollups.usage_rollup_support import child_figures, ingest_rollup_pair

_ROWS = """
    SELECT llm_calls, errors, unpriced_calls, cost_usd, tokens
    FROM user_stats_daily WHERE project_id = :p
"""


async def test_rollup_parent_excluded_from_sums_but_counted(
    browser_factory: Any, client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    start = datetime.now(UTC) - timedelta(minutes=10)
    pair = await ingest_rollup_pair(
        browser_factory, client, start, trace={"name": "answer", "user_id": "u1"}
    )
    child = await child_figures(session_factory, pair)

    async with session_factory() as db:
        await bind_project(db, pair.project_id)
        assert await refresh_user_stats(db, pair.project_id, [start.date()]) == 1
        await db.commit()

    [row] = await fetch(session_factory, _ROWS, {"p": pair.project_id})
    assert row == {
        "llm_calls": 2,
        "errors": 1,
        "unpriced_calls": 0,
        "cost_usd": child.cost_usd,
        "tokens": child.input_tokens + child.output_tokens,
    }
