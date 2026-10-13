"""A stopped worker freezes every budget in its last evaluated state, and readiness shows it.

The guard reads only the stored state, so with no evaluation pass a firing budget keeps blocking
after its spend is gone and a budget below its cap keeps allowing however much is spent.
`/health/ready` reports the heartbeat age and the alert evaluation lag; the lag is measured from
the newest finished evaluation job, so it reads null once the cleanup job has pruned them all.
"""

from datetime import timedelta

import httpx
import pytest
from sqlalchemy import text

from app.config import Settings
from app.db.rls import bypass_rls
from tests.budgets.clock import next_pass, ready_with, write_evaluation_job, write_heartbeat
from tests.budgets.support import (
    BudgetWorld,
    Sessions,
    chat,
    create_budget,
    evaluate,
    ingest_spend,
    state_of,
)

STOPPED = timedelta(minutes=10)
LAST_PASS = timedelta(minutes=5)


async def delete_spend(world: BudgetWorld) -> None:
    """Remove every span and trace of the project, as if the spend had never happened."""
    async with world.sessions() as db:
        await bypass_rls(db)
        for table in ("spans", "traces"):
            await db.execute(
                text(f"DELETE FROM {table} WHERE project_id = :id"),
                {"id": world.workspace.project_id},
            )
        await db.commit()


async def test_a_stopped_worker_freezes_a_firing_budget(budget_world: BudgetWorld) -> None:
    world = budget_world
    budget = await create_budget(world)
    await ingest_spend(world)
    await evaluate(world, next_pass(world.clock))
    assert (await chat(world)).status_code == 402

    await delete_spend(world)  # spend is now 0, and no pass runs to see it

    assert (await chat(world)).status_code == 402
    state = await state_of(world, budget)
    assert state is not None and state["state"] == "firing"
    assert world.fake.requests == []


async def test_a_stopped_worker_freezes_a_budget_below_its_cap(budget_world: BudgetWorld) -> None:
    world = budget_world
    budget = await create_budget(world)
    await evaluate(world, next_pass(world.clock))  # evaluated with nothing spent: ok

    await ingest_spend(world, steps=5)  # $0.010: ten times the cap, and no pass runs

    assert (await chat(world, scripted_tokens=0)).status_code == 200
    state = await state_of(world, budget)
    assert state is not None and state["state"] == "ok"


def assert_ready_fields(
    response: httpx.Response, *, status: int, lag: float | None, heartbeat: float = 600
) -> None:
    """The named readiness fields only: the body gains fields over time."""
    assert response.status_code == status, response.text
    body = response.json()
    assert body["status"] == ("ok" if status == 200 else "unavailable")
    assert body["worker_heartbeat_age_s"] == pytest.approx(heartbeat, abs=5)
    if lag is None:
        assert body["alert_evaluation_lag_s"] is None
    else:
        assert body["alert_evaluation_lag_s"] == pytest.approx(lag, abs=5)


async def test_a_stopped_worker_shows_in_health_ready(
    client: httpx.AsyncClient, session_factory: Sessions
) -> None:
    await write_heartbeat(session_factory, STOPPED)
    await write_evaluation_job(session_factory, LAST_PASS)

    assert_ready_fields(await client.get("/health/ready"), status=503, lag=300)


@pytest.mark.parametrize(
    ("overrides", "status", "lag"),
    [
        ({"worker_required": False}, 200, 300),  # the fields are reported all the same
        ({"alerts_evaluation_enabled": False}, 503, None),  # null: evaluation is not running
    ],
)
async def test_health_ready_reports_the_stopped_worker_under_other_settings(
    settings: Settings,
    session_factory: Sessions,
    overrides: dict[str, bool],
    status: int,
    lag: float | None,
) -> None:
    await write_heartbeat(session_factory, STOPPED)
    await write_evaluation_job(session_factory, LAST_PASS)

    assert_ready_fields(await ready_with(settings, **overrides), status=status, lag=lag)


async def test_evaluation_lag_is_null_without_a_finished_evaluation_job(
    client: httpx.AsyncClient, session_factory: Sessions
) -> None:
    """Before the first pass, or once cleanup pruned every finished one: null, which a reader
    must show as unknown, never as no lag. A queued or failed pass does not count."""
    await write_heartbeat(session_factory, STOPPED)
    await write_evaluation_job(session_factory, timedelta(minutes=1), status="queued")
    await write_evaluation_job(session_factory, timedelta(minutes=2), status="failed")

    assert_ready_fields(await client.get("/health/ready"), status=503, lag=None)


async def test_a_fresh_finished_pass_reads_as_a_small_lag(
    client: httpx.AsyncClient, session_factory: Sessions
) -> None:
    await write_heartbeat(session_factory, timedelta(seconds=5))
    await write_evaluation_job(session_factory, timedelta(seconds=30))

    assert_ready_fields(await client.get("/health/ready"), status=200, lag=30, heartbeat=5)
