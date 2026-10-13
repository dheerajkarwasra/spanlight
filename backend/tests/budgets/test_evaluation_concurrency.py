"""Evaluation passes that overlap, and passes that run out of time.

Two passes over the same breach, on two connections at once, apply its transition once: while
one pass holds the rule's row lock, the other skips the rule (`FOR UPDATE SKIP LOCKED`), so
there is exactly one alert event and one notification, and neither pass fails. A pass that runs out
of time leaves the rules it did not reach for the next pass, which starts with them: rules are
taken longest-waiting first.
"""

import asyncio
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts import evaluation as evaluation_module
from app.alerts import jobs as jobs_module
from app.alerts.evaluation import ProjectOutcome, evaluate_project
from app.alerts.jobs import evaluate_all
from app.alerts.metrics import MetricCache
from app.db.models import AlertRule
from app.db.rls import bypass_rls
from tests.budgets.clock import next_pass
from tests.budgets.support import (
    BudgetRef,
    BudgetWorld,
    create_budget,
    events_of,
    ingest_spend,
    outbox_events,
    state_of,
)

RULES = 5


async def project_pass(world: BudgetWorld, now: datetime) -> ProjectOutcome:
    """One pass over the world's project, on a connection of its own."""
    async with world.sessions() as db:
        return await evaluate_project(
            db, world.workspace.project_id, now, MetricCache(now), settings=world.settings
        )


class ParkedLock:
    """A seam on the evaluator's rule lock: the first pass to lock a rule parks there, inside
    its rule transaction with the row locked, until `release` is set. Every lock's result is
    recorded: whether that pass got the rule (False: skipped as locked by another)."""

    def __init__(self) -> None:
        self.locked = asyncio.Event()
        self.release = asyncio.Event()
        self.got_rule: list[bool] = []
        self._real = evaluation_module.lock_rule_for_evaluation

    async def __call__(
        self, db: AsyncSession, project_id: uuid.UUID, rule_id: uuid.UUID
    ) -> AlertRule | None:
        rule = await self._real(db, project_id, rule_id)
        self.got_rule.append(rule is not None)
        if len(self.got_rule) == 1:
            self.locked.set()
            await self.release.wait()
        return rule


@pytest.mark.parametrize("apart", [timedelta(0), timedelta(minutes=1)])
async def test_two_concurrent_passes_produce_exactly_one_transition(
    budget_world: BudgetWorld, monkeypatch: pytest.MonkeyPatch, apart: timedelta
) -> None:
    """The same scheduled minute twice, and a slow pass overlapping the next minute's: the
    second pass runs whole while the first holds the rule's lock, and skips the rule."""
    world = budget_world
    budget = await create_budget(world)
    await ingest_spend(world)  # $0.002 against a $0.001 cap
    first = next_pass(world.clock)
    parked = ParkedLock()
    monkeypatch.setattr(evaluation_module, "lock_rule_for_evaluation", parked)

    first_pass = asyncio.create_task(project_pass(world, first))
    try:
        await asyncio.wait_for(parked.locked.wait(), timeout=10)
        # Bounded: a pass that waited for the lock instead of skipping would never end here.
        second = await asyncio.wait_for(project_pass(world, first + apart), timeout=10)
    finally:
        parked.release.set()
    outcome = await first_pass

    assert parked.got_rule == [True, False]  # the second pass found the row locked
    assert second == ProjectOutcome(evaluated=0, transitions=0, errors=0, left=0)
    assert outcome == ProjectOutcome(evaluated=1, transitions=1, errors=0, left=0)
    state = await state_of(world, budget)
    assert state is not None
    assert (state["state"], state["last_evaluated_at"]) == ("firing", first)
    assert [event["event"] for event in await events_of(world, budget)] == ["budget.exceeded"]
    assert await outbox_events(world) == ["budget.exceeded"]


class SteppingMonotonic:
    """A monotonic clock for the evaluator where every reading costs one second.

    `evaluate_all` reads it once for its deadline and once before the project; the project
    reads it once before each rule. With `budget_seconds = 2 + n` a pass evaluates exactly `n`
    rules, however fast the database answers. The coupling to that count of reads is intended:
    if the evaluator reads the clock more or less often, `run.rules == n` in `short_pass` fails
    loudly rather than the test passing on a different cut.
    """

    def __init__(self) -> None:
        self._now = 0.0

    def monotonic(self) -> float:
        self._now += 1.0
        return self._now


async def evaluated_at(world: BudgetWorld, budgets: list[BudgetRef]) -> list[datetime | None]:
    """Each budget rule's `last_evaluated_at`, in the order of `budgets`."""
    async with world.sessions() as db:
        await bypass_rls(db)
        rows = await db.execute(
            text("SELECT rule_id, last_evaluated_at FROM alert_states WHERE rule_id = ANY(:ids)"),
            {"ids": [budget.rule_id for budget in budgets]},
        )
        found: dict[uuid.UUID, datetime] = {row.rule_id: row.last_evaluated_at for row in rows}
    return [found.get(budget.rule_id) for budget in budgets]


async def short_pass(world: BudgetWorld, monkeypatch: pytest.MonkeyPatch, rules: int) -> int:
    """A pass at the next minute with time for `rules` rules; returns the rules it left."""
    clock = SteppingMonotonic()
    monkeypatch.setattr(jobs_module, "time", clock)
    monkeypatch.setattr(evaluation_module, "time", clock)
    run = await evaluate_all(
        world.sessions, world.settings, now=next_pass(world.clock), budget_seconds=2 + rules
    )
    assert (run.errors, run.projects_failed, run.projects_left) == (0, 0, 0)
    assert run.rules == rules
    return run.rules_left


async def test_a_slow_project_leaves_rules_for_the_next_pass_in_fairness_order(
    budget_world: BudgetWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = budget_world
    budgets = [await create_budget(world, name=f"Cap {index}") for index in range(RULES)]
    # Never evaluated, so all wait equally: they go in creation order (their ids are UUIDv7).
    first, rest = budgets[:2], budgets[2:]

    assert await short_pass(world, monkeypatch, 2) == 3
    t1 = world.clock.wall()
    assert await evaluated_at(world, budgets) == [t1, t1, None, None, None]

    # The next pass starts with the three rules the first one did not reach.
    assert await short_pass(world, monkeypatch, 3) == 2
    t2 = world.clock.wall()
    assert await evaluated_at(world, rest) == [t2] * 3
    assert await evaluated_at(world, first) == [t1] * 2

    # Then the two that have waited longest since.
    assert await short_pass(world, monkeypatch, 2) == 3
    t3 = world.clock.wall()
    assert await evaluated_at(world, budgets) == [t3, t3, t2, t2, t2]
