"""A judge's monthly budget is never exceeded (ADR 0015): concurrent runs reserve under the
evaluator's lock, so only the calls that fit reach the provider, and a new month admits again.
"""

import asyncio
from datetime import timedelta
from decimal import Decimal

from app.evals.budget import month_start
from app.evals.reservation import BUDGET_EXCEEDED
from app.evals.run_settle import RunOutcome
from tests.evals.judges import JudgeWorld, answer, create_judge, estimate_usage, queue_run, run_once
from tests.evals.runs import run_row, spend

BUDGET = "0.0025"
# 540 output tokens of gpt-4.1-mini are $0.000864; with the prompt the estimate is about $0.001.
MAX_TOKENS = 540
RUNS = 5


async def test_judge_never_exceeds_monthly_budget(judge_world: JudgeWorld) -> None:
    world = judge_world
    judge = await create_judge(world, budget=BUDGET, max_tokens=MAX_TOKENS)
    budget = Decimal(BUDGET)
    assert 2 * judge.estimate <= budget < 3 * judge.estimate
    # Each answer's usage costs exactly the estimate, so a settle frees no slack and the
    # outcome does not depend on how the calls interleave.
    usage = estimate_usage(judge)
    for _ in range(2):
        answer(world.fake, usage)
    refs = [await queue_run(world, judge) for _ in range(RUNS)]

    outcomes = await asyncio.gather(*(run_once(world, judge, ref) for ref in refs))

    assert len(world.fake.requests) == 2
    done = [outcome for outcome in outcomes if outcome.status == "done"]
    assert [outcome.cost_usd for outcome in done] == [judge.estimate] * 2
    refused = [outcome for outcome in outcomes if outcome.status != "done"]
    assert refused == [RunOutcome("failed", error=BUDGET_EXCEEDED)] * (RUNS - 2)
    for ref in refs:
        row = await run_row(world.sessions, ref.run_id)
        assert row["reserved_usd"] == 0
    month = await spend(world.sessions, judge.evaluator_id, world.clock.wall())
    assert month.committed == 2 * judge.estimate <= budget

    # The next month starts from nothing.
    now = world.clock.wall()
    next_month = month_start(month_start(now) + timedelta(days=32))
    world.clock.advance((next_month - now).total_seconds() + 3600)
    answer(world.fake, usage)
    outcome = await run_once(world, judge, await queue_run(world, judge))

    assert (outcome.status, outcome.cost_usd) == ("done", judge.estimate)
    assert len(world.fake.requests) == 3
