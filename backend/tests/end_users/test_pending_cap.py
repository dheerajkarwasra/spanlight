"""The pending-trace count is capped per request, not per day (UX phase 1 final review, I1).

Failure mode: the cap sat inside the per-day probe, so a window whose days had no rows yet read
up to the cap on every day (90 days x 10,000 index entries) on each Users list and rollup
Attribution read, only to print "10,000+" afterwards. Both reads now stop after the cap in total.
"""

import json
from collections.abc import Iterator
from datetime import date, timedelta
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import ORMExecuteState

from app.attribution.queries import count_pending
from app.db.rls import bind_project
from app.end_users.days import midnight
from app.end_users.queries import PENDING_CAP, read_freshness
from tests.jobs.bypass_support import SessionFactory, new_projects, seed_llm_spans

FIRST = date(2026, 8, 1)
END = FIRST + timedelta(days=5)
PER_DAY = 4_000  # three days of it pass the cap; no single day reaches it


def _nodes(plan: dict[str, Any]) -> Iterator[dict[str, Any]]:
    yield plan
    for child in plan.get("Plans", []):
        yield from _nodes(child)


async def _traces_rows_read(db: AsyncSession, statement: Any, params: dict[str, Any]) -> int:
    """The rows the statement's scans of `traces` returned, summed over loops."""
    explain = text("EXPLAIN (ANALYZE, FORMAT JSON) " + statement.text)
    raw = (await db.execute(explain, params)).scalar_one()
    plan = (json.loads(raw) if isinstance(raw, str) else raw)[0]["Plan"]
    return sum(
        node["Actual Rows"] * node["Actual Loops"]
        for node in _nodes(plan)
        if node.get("Relation Name") == "traces"
    )


async def test_pending_count_stops_at_the_cap_across_days(session_factory: SessionFactory) -> None:
    (project_id,) = await new_projects(session_factory, 1)
    for offset in range(3):
        started = midnight(FIRST + timedelta(days=offset)) + timedelta(hours=10)
        await seed_llm_spans(session_factory, project_id, PER_DAY, started_at=started, user_id="u")

    captured: list[tuple[Any, dict[str, Any]]] = []

    def capture(state: ORMExecuteState) -> None:
        captured.append((state.statement, dict(state.parameters or {})))

    async with session_factory() as db:
        await bind_project(db, project_id)
        event.listen(db.sync_session, "do_orm_execute", capture)
        fresh = await read_freshness(db, project_id, FIRST, END, None)
        pending = await count_pending(db, project_id, "trace_name", None, (FIRST, END), None)
        event.remove(db.sync_session, "do_orm_execute", capture)

        assert fresh.computed_at is None
        assert (fresh.pending_traces, pending) == (PENDING_CAP, PENDING_CAP)
        assert len(captured) == 2
        for statement, params in captured:
            assert await _traces_rows_read(db, statement, params) <= PENDING_CAP
