"""`refresh_attribution` lists the projects with recent traces with the bypass, then refreshes
each one bound to itself. Each project's rows must sum that project's traces only, even when two
projects carry the same metadata value.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.attribution.jobs import refresh_all
from app.db.rls import bypass_rls
from tests.attribution.support import SpanSeed, TraceSeed, seed_traces
from tests.jobs.bypass_support import SessionFactory, execute, fetch, new_projects, visible_to

_ROLLUPS = (
    "SELECT project_id, group_kind, group_key, group_value, sum(traces) AS traces, "
    "sum(cost_usd) AS cost_usd FROM attribution_rollups_daily "
    "WHERE group_kind = 'metadata' GROUP BY 1, 2, 3, 4"
)


async def _seed(
    session_factory: SessionFactory,
    project_id: uuid.UUID,
    started_at: datetime,
    costs: list[str],
) -> None:
    seeds = {
        f"t{index}": TraceSeed(
            started_at,
            (SpanSeed("llm", started_at, Decimal(cost), 1, 1),),
            metadata={"customer": "acme"},
        )
        for index, cost in enumerate(costs)
    }
    async with session_factory() as db:
        await bypass_rls(db)
        await seed_traces(db, project_id, seeds)
        await db.commit()
    await execute(
        session_factory,
        "UPDATE projects SET promoted_metadata_keys = '{customer}' WHERE id = :id",
        {"id": project_id},
    )


async def test_rollups_are_computed_per_project_for_the_same_metadata_value(
    session_factory: SessionFactory,
) -> None:
    now = datetime.now(UTC)
    started = now - timedelta(minutes=5)
    a, b, idle = await new_projects(session_factory, 3)
    await _seed(session_factory, a, started, ["0.01", "0.01"])
    await _seed(session_factory, b, started, ["0.02", "0.02", "0.02"])
    await _seed(session_factory, idle, now - timedelta(days=10), ["1"])

    run = await refresh_all(session_factory, now=now)

    assert (run.projects, run.projects_failed) == (2, 0)  # no trace since yesterday, no project
    rollups = {
        (row["project_id"], row["group_key"], row["group_value"]): (row["traces"], row["cost_usd"])
        for row in await fetch(session_factory, _ROLLUPS)
    }
    assert rollups == {
        (a, "customer", "acme"): (2, Decimal("0.02")),
        (b, "customer", "acme"): (3, Decimal("0.06")),
    }
    seen_by_a = await visible_to(session_factory, a, _ROLLUPS)
    assert [row["project_id"] for row in seen_by_a] == [a]
    seen_by_b = await visible_to(session_factory, b, _ROLLUPS)
    assert [row["project_id"] for row in seen_by_b] == [b]
