"""When a firing budget stops firing without its spend going down, and when a pass must not
move it at all.

A new period unblocks at once, with no evaluation, and the next pass resolves the old period's
alert. Editing what a firing budget caps, disabling it or deleting it resolves it at once;
renaming it does not. A pass older than the one that last judged the rule changes nothing,
unless the stored time is too far ahead to be anything but a fast clock.
"""

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest

from app.budgets.periods import period_start
from app.budgets.types import BudgetPeriod
from tests.budgets.support import (
    STEP_COST,
    BudgetRef,
    BudgetWorld,
    count_rows,
    create_budget,
    evaluate,
    events_of,
    firing_budget,
    guard_decision,
    ingest_spend,
    outbox_events,
    state_of,
)

Edit = Callable[[BudgetWorld, BudgetRef], Awaitable[None]]


async def _patch(world: BudgetWorld, budget: BudgetRef, body: dict[str, object]) -> None:
    response = await world.workspace.owner.patch(
        f"{world.project_url}/budgets/{budget.id}", json=body
    )
    assert response.status_code == 200, response.text


async def raise_amount(world: BudgetWorld, budget: BudgetRef) -> None:
    await _patch(world, budget, {"amount_usd": "0.005"})


async def disable(world: BudgetWorld, budget: BudgetRef) -> None:
    await _patch(world, budget, {"enabled": False})


async def delete(world: BudgetWorld, budget: BudgetRef) -> None:
    response = await world.workspace.owner.delete(f"{world.project_url}/budgets/{budget.id}")
    assert response.status_code == 204, response.text


def logged(caplog: pytest.LogCaptureFixture, event: str) -> list[tuple[str, dict[str, object]]]:
    """The level and fields of every `event` logged; structlog hands the stdlib handler the
    event dict itself as the record's message."""
    return [
        (record.levelname, record.msg)
        for record in caplog.records
        if isinstance(record.msg, dict) and record.msg.get("event") == event
    ]


async def assert_state(world: BudgetWorld, budget: BudgetRef, state: str, since: datetime) -> None:
    stored = await state_of(world, budget)
    assert stored is not None
    assert (stored["state"], stored["since"]) == (state, since)


@pytest.mark.parametrize("period", [BudgetPeriod.DAILY, BudgetPeriod.MONTHLY])
async def test_period_rollover_unblocks_without_evaluation_and_resolves_on_the_next_pass(
    budget_world: BudgetWorld, period: BudgetPeriod, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    world = budget_world
    rollover = period_start(period, datetime.now(UTC))  # today 00:00, or the 1st 00:00
    fired_at = rollover - timedelta(minutes=10)  # 23:50 the day (or month) before
    after = rollover + timedelta(minutes=1)  # 00:01
    budget = await create_budget(world, period=period.value)
    await ingest_spend(world, start=rollover - timedelta(minutes=20))
    await evaluate(world, fired_at)
    await assert_state(world, budget, "firing", fired_at)
    assert not (await guard_decision(world, rollover - timedelta(minutes=1))).allowed

    # The new period: the guard allows before any pass runs.
    assert (await guard_decision(world, after)).allowed

    await evaluate(world, after)
    await assert_state(world, budget, "ok", after)
    # Resolved as a rollover, not because this period's spend read below the cap.
    [(_, closed)] = logged(caplog, "alert_closed_by_period_rollover")
    assert closed["rule_id"] == str(budget.rule_id)
    events = await events_of(world, budget)
    assert [(event["event"], event["resolved_at"]) for event in events] == [
        ("budget.exceeded", after)
    ]
    assert await outbox_events(world) == ["budget.exceeded", "alert.resolved"]

    await evaluate(world, after + timedelta(minutes=1))  # resolved once, not again
    assert await outbox_events(world) == ["budget.exceeded", "alert.resolved"]


@pytest.mark.parametrize("edit", [raise_amount, disable, delete])
async def test_editing_disabling_or_deleting_a_firing_budget_resolves_at_once(
    budget_world: BudgetWorld, edit: Edit
) -> None:
    world = budget_world
    budget, _ = await firing_budget(world)
    assert not (await guard_decision(world, world.clock.wall())).allowed

    await edit(world, budget)

    assert (await guard_decision(world, world.clock.wall())).allowed  # no pass needed
    assert await outbox_events(world) == ["budget.exceeded", "alert.resolved"]
    if edit is delete:
        for table in ("budgets", "alert_rules", "alert_states", "alert_events"):
            assert await count_rows(world, table, budget) == 0, table
        return
    stored = await state_of(world, budget)
    assert stored is not None and stored["state"] == "ok"
    [event] = await events_of(world, budget)
    assert event["resolved_at"] is not None


async def test_renaming_a_firing_budget_resolves_nothing(budget_world: BudgetWorld) -> None:
    world = budget_world
    budget, fired_at = await firing_budget(world)

    await _patch(world, budget, {"name": "Renamed cap"})

    await assert_state(world, budget, "firing", fired_at)
    assert not (await guard_decision(world, world.clock.wall())).allowed
    assert await outbox_events(world) == ["budget.exceeded"]
    [event] = await events_of(world, budget)
    assert event["resolved_at"] is None


def earlier_minute() -> datetime:
    """A minute boundary with a whole day's room around it: yesterday 22:00 UTC."""
    return period_start(BudgetPeriod.DAILY, datetime.now(UTC)) - timedelta(hours=2)


async def test_an_older_pass_does_not_move_state_back(budget_world: BudgetWorld) -> None:
    world = budget_world
    t = earlier_minute()
    budget = await create_budget(world)
    # Spent between T and T+60: the pass at T+60 sees it, a pass at T would read nothing.
    await ingest_spend(world, start=t + timedelta(seconds=20))
    await evaluate(world, t + timedelta(seconds=60))
    before = await state_of(world, budget)
    assert before is not None
    assert (before["state"], before["last_value"]) == ("firing", STEP_COST)

    late = await evaluate(world, t)  # the older pass, running late

    assert late.rules == 0  # skipped: judged by a newer pass
    assert await state_of(world, budget) == before
    assert await outbox_events(world) == ["budget.exceeded"]


async def test_a_stored_time_far_ahead_is_evaluated_anyway(
    budget_world: BudgetWorld, caplog: pytest.LogCaptureFixture
) -> None:
    """Ten minutes ahead is no newer pass (those are at most two intervals ahead): a clock ran
    fast, and freezing the rule until real time caught up would be worse."""
    world = budget_world
    t = earlier_minute()
    budget = await create_budget(world)
    await ingest_spend(world, start=t + timedelta(seconds=20))
    await evaluate(world, t + timedelta(minutes=10))  # the fast clock's pass: firing

    run = await evaluate(world, t)

    assert run.rules == 1
    stored = await state_of(world, budget)
    assert stored is not None
    assert (stored["last_evaluated_at"], stored["last_value"]) == (t, 0)
    [warning] = logged(caplog, "alert_state_from_future")
    assert warning[0] == "WARNING"
    assert warning[1]["seconds_ahead"] == 600
