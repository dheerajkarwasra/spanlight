"""What a judge run is charged when its call fails (ADR 0015), driven through `run_judge` and the
gateway against the fake provider: nothing for a call the provider refused or the gateway never
sent, the reported usage's cost when there is one, else the reserved estimate. Every failure
releases the reservation.
"""

import asyncio
from decimal import Decimal

import pytest

from app.evals.evaluators import judge as judge_module
from app.evals.run_settle import RunOutcome
from app.evals.runner_judge import CANCELLED
from app.evals.verdict import VERDICT_UNPARSEABLE
from app.gateway.settle_helpers import drain_settling
from tests.budgets.support import BudgetWorld, firing_budget
from tests.evals.judges import (
    VERDICT,
    Judge,
    JudgeWorld,
    ParkingSessions,
    answer,
    create_judge,
    queue_run,
    run_once,
)
from tests.evals.runs import run_row, spend
from tests.gateway_support import Stall, read_gateway_spans

BUDGET = "1.0"
# 10 prompt and 5 completion tokens of gpt-4.1-mini: 10 x $0.40 + 5 x $1.60 per million.
SMALL_USAGE = (10, 5)
SMALL_USAGE_COST = Decimal("0.000012")


async def _failed_run(world: JudgeWorld, judge: Judge, *, error: str, charge: Decimal) -> None:
    """Run one queued run and check it failed with `error`, charged `charge`, holding nothing."""
    ref = await queue_run(world, judge)
    outcome = await run_once(world, judge, ref)
    assert outcome == RunOutcome("failed", charge, error)
    row = await run_row(world.sessions, ref.run_id)
    assert (row["status"], row["error"]) == ("failed", error)
    assert (row["cost_usd"], row["reserved_usd"]) == (charge, 0)


async def test_failed_call_charges(
    judge_world: JudgeWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = judge_world
    judge = await create_judge(world, budget=BUDGET)

    # The provider refused the call: nothing was generated.
    answer(world.fake, None, content="", status=429)
    await _failed_run(world, judge, error="judge_status_429", charge=Decimal(0))

    # An answer with no verdict in it was generated and billed: its usage's cost.
    answer(world.fake, SMALL_USAGE, content="I would rather not say.")
    await _failed_run(world, judge, error=VERDICT_UNPARSEABLE, charge=SMALL_USAGE_COST)
    answer(world.fake, SMALL_USAGE, content="")
    await _failed_run(world, judge, error="judge_no_answer", charge=SMALL_USAGE_COST)

    # A call that timed out after it was sent may have been billed, with no usage to say how
    # much: the whole estimate.
    monkeypatch.setattr(judge_module, "CALL_TIMEOUT_MS", 1000)  # the shortest a route allows
    world.fake.reply_json({}, before_headers=Stall())
    await _failed_run(world, judge, error="judge_status_504", charge=judge.estimate)

    assert len(world.fake.requests) == 4
    month = await spend(world.sessions, judge.evaluator_id, world.clock.wall())
    assert month.reserved == 0
    assert month.spent == 2 * SMALL_USAGE_COST + judge.estimate


async def _until_sent(world: JudgeWorld) -> None:
    for _ in range(500):  # up to 5 s for the gateway to send the call
        if world.fake.requests:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the judge call never reached the provider")


async def test_cancelled_judge_call_settles_at_the_estimate(judge_world: JudgeWorld) -> None:
    world = judge_world
    judge = await create_judge(world, budget=BUDGET)
    ref = await queue_run(world, judge)
    world.fake.reply_json({}, before_headers=Stall())
    sessions = ParkingSessions(world.sessions)
    task = asyncio.create_task(run_once(world, judge, ref, deps=sessions.deps(world.deps)))
    await _until_sent(world)

    # The worker cancels the task mid-call, then again while the settle is under way (its
    # session parked): only the shield keeps the second cancellation from losing the settle.
    sessions.park()
    task.cancel()
    await asyncio.wait_for(sessions.parked.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    sessions.release()
    await drain_settling()

    row = await run_row(world.sessions, ref.run_id)
    assert (row["status"], row["error"]) == ("failed", CANCELLED)
    assert (row["cost_usd"], row["reserved_usd"]) == (judge.estimate, 0)
    spans = await read_gateway_spans(world.sessions, world.workspace.project_id)
    assert len(spans) == 1


async def test_a_project_block_budget_stops_a_judge_before_sending(
    budget_world: BudgetWorld, judge_world: JudgeWorld
) -> None:
    world = judge_world
    judge = await create_judge(world, budget=BUDGET)
    await firing_budget(budget_world)
    ref = await queue_run(world, judge)
    answer(world.fake, SMALL_USAGE, content=VERDICT)  # must never be used

    outcome = await run_once(world, judge, ref)

    assert outcome == RunOutcome("failed", Decimal(0), "gateway_budget_blocked")
    row = await run_row(world.sessions, ref.run_id)
    assert (row["status"], row["error"]) == ("failed", "gateway_budget_blocked")
    assert (row["cost_usd"], row["reserved_usd"]) == (0, 0)
    assert world.fake.requests == []
    assert world.fake.pending == 1
