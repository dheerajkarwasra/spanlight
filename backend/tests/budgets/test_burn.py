"""A budget's burn: its spend per UTC day of the current period, and the running total.

The app's clock is pinned to 12:00 UTC on the third day of last month, so a monthly budget's
period so far is three days: two whole ones and today's first twelve hours. Each day is one
window of the budget's spend metric, read like the evaluation reads it (hourly rollups before
the split, raw spans after it), so the points add up to what the budget measures.
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text

from app.budgets.periods import period_start
from app.budgets.types import BudgetPeriod
from app.db.rls import bypass_rls
from app.rollups.jobs import roll_up_all
from tests.budgets.support import (
    PRICED_MODEL,
    STEP_COST,
    STEP_TOKENS,
    BudgetRef,
    BudgetWorld,
    create_budget,
    ingest_spend,
)
from tests.gateway_support import ManualClock
from tests.helpers import create_workspace, new_trace_id, span

UNPRICED_MODEL = "house-model-1"  # not in the price book
DAY = timedelta(days=1)


def _month_start() -> datetime:
    this_month = period_start(BudgetPeriod.MONTHLY, datetime.now(UTC))
    return period_start(BudgetPeriod.MONTHLY, this_month - DAY)


MONTH = _month_start()
NOW = MONTH + 2 * DAY + timedelta(hours=12)
NEXT_MONTH = (MONTH + 32 * DAY).replace(day=1)
DAYS = [(MONTH + n * DAY).date() for n in range(3)]


@pytest.fixture
def app_clock() -> ManualClock:
    return ManualClock(start=NOW)


async def ingest_at(
    world: BudgetWorld,
    start: datetime,
    *,
    model: str = PRICED_MODEL,
    metadata: dict[str, str] | None = None,
) -> None:
    """One trace with one span of `STEP_TOKENS` input tokens ($0.002 when priced)."""
    usage = {"input_tokens": STEP_TOKENS, "output_tokens": 0, "cached_tokens": 0}
    trace = {"name": "answer", "environment": "prod", "metadata": metadata or {}}
    body = span(trace_id=new_trace_id(), start=start, model=model, usage=usage, trace=trace)
    response = await world.ingest.post("/v1/traces", json={"spans": [body]})
    assert response.status_code == 200, response.text
    assert response.json()["accepted"] == 1


async def burn(world: BudgetWorld, budget: BudgetRef) -> dict[str, Any]:
    response = await world.workspace.owner.get(f"{world.project_url}/budgets/{budget.id}/burn")
    assert response.status_code == 200, response.text
    return dict(response.json())


def money(value: str | None) -> Decimal | None:
    return None if value is None else Decimal(value)


def series(body: dict[str, Any]) -> list[tuple[date, Decimal | None, Decimal | None]]:
    return [
        (date.fromisoformat(p["day"]), money(p["spend_usd"]), money(p["cumulative_usd"]))
        for p in body["points"]
    ]


async def roll_up(world: BudgetWorld) -> None:
    """Roll up the period's hours, as the worker would have by `NOW`."""
    await roll_up_all(world.sessions, now=NOW)


async def test_burn_project_scope_cumulative(budget_world: BudgetWorld) -> None:
    world = budget_world
    budget = await create_budget(world, period="monthly", amount=Decimal(1))
    await ingest_at(world, MONTH - timedelta(seconds=1))  # last month's period
    await ingest_at(world, MONTH + timedelta(hours=10))
    await ingest_at(world, MONTH + DAY)  # the second day's first instant
    await ingest_at(world, MONTH + DAY + timedelta(hours=23, minutes=59))
    await ingest_at(world, NOW - timedelta(minutes=30))  # after the rollup split: read raw
    await roll_up(world)

    body = await burn(world, budget)

    assert datetime.fromisoformat(body["period_start"]) == MONTH
    assert datetime.fromisoformat(body["resets_at"]) == NEXT_MONTH
    assert Decimal(body["amount_usd"]) == Decimal(1)
    assert series(body) == [
        (DAYS[0], STEP_COST, STEP_COST),
        (DAYS[1], 2 * STEP_COST, 3 * STEP_COST),
        (DAYS[2], STEP_COST, 4 * STEP_COST),
    ]


async def test_burn_metadata_scope_counts_only_matching_traces(
    budget_world: BudgetWorld,
) -> None:
    world = budget_world
    budget = await create_budget(
        world, period="monthly", amount=Decimal(1), scope="metadata", scope_id="customer_id=acme"
    )
    acme = {"customer_id": "acme"}
    await ingest_at(world, MONTH + timedelta(hours=1), metadata=acme)
    await ingest_at(world, MONTH + timedelta(hours=2), metadata={"customer_id": "globex"})
    await ingest_at(world, MONTH + DAY + timedelta(hours=3))
    await ingest_at(world, NOW - timedelta(hours=5), metadata=acme)

    assert series(await burn(world, budget)) == [
        (DAYS[0], STEP_COST, STEP_COST),
        (DAYS[1], Decimal(0), STEP_COST),
        (DAYS[2], STEP_COST, 2 * STEP_COST),
    ]


async def test_burn_days_stop_at_now(budget_world: BudgetWorld) -> None:
    world = budget_world
    monthly = await create_budget(world, period="monthly", amount=Decimal(1), name="Month")
    daily = await create_budget(world, period="daily", amount=Decimal(1), name="Day")
    await ingest_at(world, NOW + timedelta(minutes=5))  # later today: not spent yet at `now`
    await roll_up(world)

    assert series(await burn(world, monthly)) == [
        (DAYS[0], Decimal(0), Decimal(0)),
        (DAYS[1], Decimal(0), Decimal(0)),
        (DAYS[2], Decimal(0), Decimal(0)),
    ]
    body = await burn(world, daily)
    assert datetime.fromisoformat(body["period_start"]) == MONTH + 2 * DAY
    assert datetime.fromisoformat(body["resets_at"]) == MONTH + 3 * DAY
    assert series(body) == [(DAYS[2], Decimal(0), Decimal(0))]


async def test_burn_unknown_day_is_null_and_cumulative_carries(
    budget_world: BudgetWorld,
) -> None:
    world = budget_world
    budget = await create_budget(world, period="monthly", amount=Decimal(1))
    await ingest_at(world, MONTH + timedelta(hours=4), model=UNPRICED_MODEL)
    await ingest_at(world, MONTH + DAY + timedelta(hours=4))
    await ingest_at(world, MONTH + DAY + timedelta(hours=5), model=UNPRICED_MODEL)
    await ingest_at(world, MONTH + 2 * DAY + timedelta(hours=4), model=UNPRICED_MODEL)
    await roll_up(world)

    assert series(await burn(world, budget)) == [
        (DAYS[0], None, None),  # spans, none priced: unknown, not $0
        (DAYS[1], STEP_COST, STEP_COST),  # the priced part, a lower bound
        (DAYS[2], None, STEP_COST),
    ]


async def created_on(world: BudgetWorld, created_at: datetime) -> None:
    async with world.sessions() as db:
        await bypass_rls(db)
        await db.execute(
            text("UPDATE projects SET created_at = :at WHERE id = :id"),
            {"at": created_at, "id": world.workspace.project_id},
        )
        await db.commit()


async def test_burn_forecast_at_the_trailing_days_rate(budget_world: BudgetWorld) -> None:
    world = budget_world
    world.clock.advance((8 * DAY).total_seconds())  # noon on the 11th day of the month
    now = world.clock.wall()
    await created_on(world, MONTH - DAY)
    steady = await create_budget(
        world, period="monthly", amount=Decimal(40), scope="user", scope_id="steady"
    )
    tight = await create_budget(
        world,
        period="monthly",
        amount=Decimal(25),
        scope="user",
        scope_id="steady",
        name="Tight",
    )
    quiet = await create_budget(
        world, period="monthly", amount=Decimal(40), scope="user", scope_id="quiet", name="Quiet"
    )
    for day in range(10):  # $1 a day on each of the first ten days
        await ingest_spend(
            world, start=MONTH + day * DAY + timedelta(hours=1), user="steady", tokens=2_500_000
        )
        if day != 3:  # $0 on the 4th, the first of the last 7 complete days
            await ingest_spend(
                world, start=MONTH + day * DAY + timedelta(hours=1), user="quiet", tokens=2_500_000
            )
    await roll_up_all(world.sessions, now=now)

    remaining = Decimal((NEXT_MONTH - now) / DAY)
    body = await burn(world, steady)
    assert (money(body["forecast_usd"]), body["forecast_basis_days"]) == (10 + remaining, 7)
    assert body["forecast_crosses_on"] is None  # 10 + 1 x remaining stays under $40
    body = await burn(world, tight)
    assert date.fromisoformat(body["forecast_crosses_on"]) == (now + 15 * DAY).date()
    body = await burn(world, quiet)  # the $0 day is a real day: $6 over 7 days
    expected = (9 + Decimal(6) / 7 * remaining).quantize(Decimal("0.00000001"))
    assert (money(body["forecast_usd"]), body["forecast_basis_days"]) == (expected, 7)

    await created_on(world, MONTH + 8 * DAY + timedelta(hours=6))  # two days before today
    body = await burn(world, steady)
    assert (body["forecast_usd"], body["forecast_basis_days"]) == (None, 2)
    assert body["forecast_crosses_on"] is None


async def test_burn_other_project_budget_is_404(budget_world: BudgetWorld) -> None:
    world = budget_world
    budget = await create_budget(world, period="monthly", amount=Decimal(1))
    other = await create_workspace(world.workspace.owner, org_name="Elsewhere")

    response = await world.workspace.owner.get(
        f"/api/v1/projects/{other.project_id}/budgets/{budget.id}/burn"
    )
    assert response.status_code == 404, response.text
    missing = await world.workspace.owner.get(f"{world.project_url}/budgets/{uuid.uuid4()}/burn")
    assert missing.status_code == 404, missing.text
