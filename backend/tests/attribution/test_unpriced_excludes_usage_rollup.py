"""Attribution's `unpriced_calls` leaves usage rollups out, as the metrics and the Users list do.

Failure mode: an outer span that repeats the usage of the priced call it wraps has no cost of
its own, and attribution counted it as an unpriced call, so the page reported "1 unpriced" where
Overview reported none.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.attribution.aggregate_sql import grouped_cte
from app.db.rls import bypass_rls
from app.ingest.store import _recompute_rollups
from tests.attribution.support import SpanSeed, TraceSeed, seed_traces
from tests.jobs.bypass_support import SessionFactory, fetch, new_projects

# The later span of a trace becomes the child of the earlier one.
_NEST = text(
    """
    UPDATE spans AS child SET parent_span_id = parent.span_id
    FROM spans AS parent
    WHERE child.project_id = :project_id AND child.trace_id = :trace_id
      AND parent.project_id = :project_id AND parent.trace_id = :trace_id
      AND parent.started_at < child.started_at
    """
)


async def nest_and_recompute(db: AsyncSession, project_id: uuid.UUID, trace_id: str) -> None:
    """Nest a two-span trace's later span under its earlier one, then recompute as ingestion
    does, which flags the outer span as a usage rollup (both carry tokens)."""
    await db.execute(_NEST, {"project_id": project_id, "trace_id": trace_id})
    await _recompute_rollups(db, project_id, [trace_id])


async def test_unpriced_calls_excludes_usage_rollup_spans(session_factory: SessionFactory) -> None:
    [project_id] = await new_projects(session_factory, 1)
    start = datetime(2026, 9, 10, 10, tzinfo=UTC)
    outer = SpanSeed("llm", start, None, 60, 6)
    inner = SpanSeed("llm", start.replace(second=1), Decimal("0.10"), 50, 5)
    async with session_factory() as db:
        await bypass_rls(db)
        ids = await seed_traces(db, project_id, {"pair": TraceSeed(start, (outer, inner))})
        await nest_and_recompute(db, project_id, ids["pair"])
        await db.commit()

    flags = await fetch(
        session_factory,
        "SELECT usage_rollup FROM spans WHERE project_id = :p ORDER BY started_at",
        {"p": project_id},
    )
    assert [row["usage_rollup"] for row in flags] == [True, False]

    cte = grouped_cte("trace_name", by_day=False, with_environment=False)
    window = {"start": start.replace(hour=0), "end": start.replace(hour=23)}
    [row] = await fetch(
        session_factory, f"WITH {cte} SELECT * FROM grouped", {"project_id": project_id, **window}
    )
    assert (row["llm_calls"], row["unpriced_calls"]) == (2, 0)
    assert row["cost_usd"] == Decimal("0.10")
