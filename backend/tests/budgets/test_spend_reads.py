"""A budget's spend is the exact cost of the spans in its period, across the rollup split.

The evaluator reads the whole hours before `floor_hour(now) - 1 h` from the hourly rollups and
everything after from raw spans (`app.alerts.metrics`). A daily budget's window starts at
00:00, on the hour, so it has no partial head hour: its rolled-up part runs from 00:00 to the
split, and its raw part from the split to the pass's minute. Spend sits on every boundary
there; the sum must count each span once and miss none. Spend that cannot be priced is unknown
(`null`), never zero: a budget whose spans are all unpriced never fires, and with some priced
the spend read is the priced part, a lower bound.
"""

from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import text

from app.budgets.periods import period_start
from app.budgets.types import BudgetPeriod
from app.db.rls import bypass_rls
from app.rollups.jobs import roll_up_all
from tests.budgets.clock import next_pass
from tests.budgets.support import (
    PRICED_MODEL,
    BudgetRef,
    BudgetWorld,
    create_budget,
    evaluate,
    events_of,
    ingest_spend,
    outbox_events,
    state_of,
)

UNPRICED_MODEL = "house-model-1"  # not in the price book
# $0.40 per million input tokens of `PRICED_MODEL`: 1,000 tokens cost $0.0004.
UNIT_TOKENS = 1000
UNIT_COST = Decimal("0.0004")
# The pass runs at 12:31 of the pinned day, so the split is 11:00: 00:00-11:00 comes from the
# rollups (the hours 00, 09 and 10 hold spend), 11:00-12:31 from raw spans.
PASS_AT = timedelta(hours=12, minutes=31)
SPLIT_AT = timedelta(hours=11)
# Where spend sits, from the day's start. Each point costs a different power of two units, so a
# point counted twice or missed gives a sum no correct read can give.
INSIDE = [
    timedelta(0),  # the window's first instant, in a rolled-up hour
    timedelta(hours=9),  # a rolled-up hour's first instant
    timedelta(hours=10) - timedelta(milliseconds=1),  # the same hour's last
    timedelta(hours=10),  # the next rolled-up hour, right after
    SPLIT_AT - timedelta(seconds=1),  # the last rolled-up second
    SPLIT_AT,  # the first raw instant
    timedelta(hours=12),  # the hour the pass runs in
    PASS_AT - timedelta(seconds=1),  # the last second the pass reads
]
OUTSIDE = [
    -timedelta(seconds=1),  # yesterday's last second: another period
    PASS_AT,  # the pass reads windows that end on its minute: the next pass counts it
]


def day_start(world: BudgetWorld) -> datetime:
    return period_start(BudgetPeriod.DAILY, world.clock.wall())


async def ingest_points(
    world: BudgetWorld,
    points: list[timedelta],
    *,
    model: str = PRICED_MODEL,
    first_power: int = 0,
) -> list[Decimal]:
    """One span at each point (from the day's start), the n-th of 2^(first_power + n) units;
    returns each one's cost, as priced if `model` is."""
    start = day_start(world)
    costs = []
    for power, point in enumerate(points, start=first_power):
        await ingest_spend(world, start=start + point, model=model, tokens=UNIT_TOKENS * 2**power)
        costs.append(UNIT_COST * 2**power)
    return costs


async def roll_up_and_pass(world: BudgetWorld) -> datetime:
    """Roll up every hour of the day (the current one too, as the job does), then run the pass
    at 12:31; returns its time."""
    pass_at = day_start(world) + PASS_AT
    world.clock.advance((pass_at - timedelta(seconds=30) - world.clock.wall()).total_seconds())
    assert next_pass(world.clock) == pass_at
    projects, _ = await roll_up_all(world.sessions, now=pass_at)
    assert projects == 1
    await evaluate(world, pass_at)
    return pass_at


async def rolled_up_hours(world: BudgetWorld) -> list[datetime]:
    async with world.sessions() as db:
        await bypass_rls(db)
        rows = await db.scalars(
            text(
                "SELECT DISTINCT bucket_start FROM span_rollups_hourly "
                "WHERE project_id = :id ORDER BY bucket_start"
            ),
            {"id": world.workspace.project_id},
        )
        return list(rows)


async def delete_spans_before(world: BudgetWorld, moment: datetime) -> None:
    """Remove the raw spans started before `moment`, leaving their rollups."""
    async with world.sessions() as db:
        await bypass_rls(db)
        await db.execute(
            text("DELETE FROM spans WHERE project_id = :id AND started_at < :moment"),
            {"id": world.workspace.project_id, "moment": moment},
        )
        await db.commit()


async def api_spent(world: BudgetWorld, budget: BudgetRef) -> Decimal | None:
    """The budget's `spent_usd` as the budgets API shows it."""
    response = await world.workspace.owner.get(f"{world.project_url}/budgets/{budget.id}")
    assert response.status_code == 200, response.text
    spent = response.json()["state"]["spent_usd"]
    return None if spent is None else Decimal(spent)


async def test_spend_reads_match_span_costs_across_the_rollup_split(
    budget_world: BudgetWorld,
) -> None:
    world = budget_world
    budget = await create_budget(world, amount=Decimal(1))
    outside = await ingest_points(world, OUTSIDE)
    expected = sum(await ingest_points(world, INSIDE, first_power=len(OUTSIDE)), Decimal(0))

    await roll_up_and_pass(world)

    day = day_start(world)
    hours = await rolled_up_hours(world)
    assert {day, day + timedelta(hours=9), day + timedelta(hours=10)} <= set(hours)
    state = await state_of(world, budget)
    assert state is not None
    assert (state["state"], state["last_value"]) == ("ok", expected)
    assert await api_spent(world, budget) == expected

    # The hours before the split are read from the rollups alone: without their raw spans the
    # next pass reads the same spend.
    await delete_spans_before(world, day + SPLIT_AT)
    await evaluate(world, next_pass(world.clock))
    state = await state_of(world, budget)
    assert state is not None
    assert state["last_value"] == expected + outside[1]  # and now the span at 12:31


async def test_unpriced_spend_reads_unknown_and_never_fires(budget_world: BudgetWorld) -> None:
    world = budget_world
    budget = await create_budget(world)  # $0.001: any known spend here would fire it
    await ingest_points(world, INSIDE, model=UNPRICED_MODEL)

    await roll_up_and_pass(world)

    state = await state_of(world, budget)
    assert state is not None
    assert (state["state"], state["last_value"]) == ("ok", None)
    assert await events_of(world, budget) == []
    assert await outbox_events(world) == []
    assert await api_spent(world, budget) is None


async def test_mixed_spend_reads_the_priced_part_as_a_lower_bound(
    budget_world: BudgetWorld,
) -> None:
    world = budget_world
    budget = await create_budget(world, amount=Decimal(1))
    priced = sum(await ingest_points(world, INSIDE), Decimal(0))
    await ingest_points(world, INSIDE, model=UNPRICED_MODEL)

    await roll_up_and_pass(world)

    state = await state_of(world, budget)
    assert state is not None
    assert state["last_value"] == priced
    assert await api_spent(world, budget) == priced
