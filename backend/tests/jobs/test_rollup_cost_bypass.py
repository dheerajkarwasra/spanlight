"""`rollup_cost_daily` lists the projects with LLM spans with the bypass, then rolls each one up
bound to itself. Each project's rows must sum that project's spans only, even when two projects
call the same model on the same day.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.reconciliation.rollup_jobs import rollup_all
from tests.jobs.bypass_support import (
    SessionFactory,
    fetch,
    new_projects,
    seed_llm_spans,
    seed_row,
    visible_to,
)

_ROLLUPS = (
    "SELECT project_id, model, sum(calls) AS calls, sum(cost_usd) AS cost_usd "
    "FROM cost_rollups_daily GROUP BY 1, 2"
)


async def test_cost_rollups_are_per_project_even_for_the_same_model(
    session_factory: SessionFactory,
) -> None:
    now = datetime.now(UTC)
    started = now - timedelta(minutes=5)
    a, b, idle = await new_projects(session_factory, 3)
    await seed_llm_spans(session_factory, a, 2, started_at=started, cost_usd=Decimal("0.01"))
    await seed_llm_spans(session_factory, b, 3, started_at=started, cost_usd=Decimal("0.02"))
    await seed_llm_spans(
        session_factory, idle, 1, started_at=now - timedelta(days=10), cost_usd=Decimal("1")
    )

    run = await rollup_all(session_factory, now=now)

    assert run.projects == 2 and run.projects_failed == 0  # no span in the window, no project
    rollups = {
        (row["project_id"], row["model"]): (row["calls"], row["cost_usd"])
        for row in await fetch(session_factory, _ROLLUPS)
    }
    assert rollups == {
        (a, "model-a"): (2, Decimal("0.02000000")),
        (b, "model-a"): (3, Decimal("0.06000000")),
    }


async def test_a_project_cannot_read_the_cost_rollups_written_for_another(
    session_factory: SessionFactory,
) -> None:
    now = datetime.now(UTC)
    a, b = await new_projects(session_factory, 2)
    await seed_llm_spans(session_factory, a, 1, started_at=now - timedelta(minutes=1))
    await seed_llm_spans(session_factory, b, 1, started_at=now - timedelta(minutes=1))
    await rollup_all(session_factory, now=now)

    seen_by_a = await visible_to(session_factory, a, _ROLLUPS)
    assert [row["project_id"] for row in seen_by_a] == [a]
    seen_by_b = await visible_to(session_factory, b, _ROLLUPS)
    assert [row["project_id"] for row in seen_by_b] == [b]


async def test_a_rollup_does_not_touch_the_rows_of_a_project_it_did_not_run(
    session_factory: SessionFactory,
) -> None:
    now = datetime.now(UTC)
    active, dormant = await new_projects(session_factory, 2)
    await seed_llm_spans(session_factory, active, 1, started_at=now - timedelta(minutes=1))
    # The dormant project has a rollup row and no spans; the refresh would delete it if it ran.
    kept = await seed_row(session_factory, "cost_rollups_daily", dormant)

    await rollup_all(session_factory, now=now)

    rows = await fetch(
        session_factory,
        "SELECT day, calls FROM cost_rollups_daily WHERE project_id = :project_id",
        {"project_id": dormant},
    )
    assert [(row["day"], row["calls"]) for row in rows] == [(kept["day"], kept["calls"])]
