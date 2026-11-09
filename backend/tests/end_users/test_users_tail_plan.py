"""The Users raw tail starts its trace scan at the oldest relevant recompute (final review #3).

Failure mode: the tail's only plannable lower bound was `now() - 24 hours`; the per-day
`computed_at` bound is correlated, so it filtered rows after the index scan. Every Users list
read the whole last day of traces, recomputed or not. An uncorrelated bound now starts the index
range at the oldest `computed_at` of the tail's days.
"""

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import ORMExecuteState

from app.db.rls import bind_project
from app.end_users.jobs import refresh_all
from app.end_users.queries import read_users
from tests.jobs.bypass_support import SessionFactory, new_projects, seed_llm_spans

BEFORE = 2_000


def _nodes(plan: dict[str, Any]) -> Iterator[dict[str, Any]]:
    yield plan
    for child in plan.get("Plans", []):
        yield from _nodes(child)


async def _traces_rows_scanned(db: AsyncSession, statement: Any, params: dict[str, Any]) -> int:
    """The `traces` rows the statement's scans read, filtered out or not, summed over loops."""
    explain = text("EXPLAIN (ANALYZE, FORMAT JSON) " + statement.text)
    raw = (await db.execute(explain, params)).scalar_one()
    plan = (json.loads(raw) if isinstance(raw, str) else raw)[0]["Plan"]
    return sum(
        (node["Actual Rows"] + node.get("Rows Removed by Filter", 0)) * node["Actual Loops"]
        for node in _nodes(plan)
        if node.get("Relation Name") == "traces"
    )


async def test_tail_scan_skips_recomputed_traces(session_factory: SessionFactory) -> None:
    (project_id,) = await new_projects(session_factory, 1)
    now = datetime.now(UTC)
    await seed_llm_spans(
        session_factory, project_id, BEFORE, started_at=now - timedelta(minutes=10), user_id="u"
    )
    await refresh_all(session_factory, now=datetime.now(UTC))
    await seed_llm_spans(session_factory, project_id, 3, started_at=datetime.now(UTC), user_id="u")

    captured: list[tuple[Any, dict[str, Any]]] = []

    def capture(state: ORMExecuteState) -> None:
        captured.append((state.statement, dict(state.parameters or {})))

    today = datetime.now(UTC).date()
    async with session_factory() as db:
        await bind_project(db, project_id)
        event.listen(db.sync_session, "do_orm_execute", capture)
        rows = await read_users(
            db,
            project_id,
            today - timedelta(days=2),
            today + timedelta(days=1),
            "traces",
            10,
            None,
            None,
        )
        event.remove(db.sync_session, "do_orm_execute", capture)

        assert [(row.external_user_id, row.traces) for row in rows] == [("u", BEFORE + 3)]
        ((statement, params),) = captured
        assert await _traces_rows_scanned(db, statement, params) < 100
