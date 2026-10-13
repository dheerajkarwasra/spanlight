"""A judge's reserved budget in the database (ADR 0015): reservations serialize under the
evaluator's lock, a settle returns the slack, the reaper charges what a dead worker may have
spent, and a settle that arrives after the reaper changes nothing.
"""

import asyncio
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.config import Settings
from app.core.observability import EVALUATOR_COST, EVALUATOR_RUNS
from app.db.rls import bind_project
from app.evals.budget import can_spend
from app.evals.jobs import sample_all
from app.evals.reservation import ABANDONED, BUDGET_EXCEEDED, settle_run
from app.evals.run_settle import (
    RUN_DELETED,
    RunOutcome,
    RunRef,
    observe,
    settle_failure,
    settle_success,
)
from app.evals.runner_build import RunnerDeps
from app.evals.schemas import EvalResult
from app.pricing.tokens import estimate_text_tokens
from tests.evals.runs import (
    Sessions,
    delete_evaluator,
    insert_run,
    judge_evaluator,
    reaper_world,
    reserve,
    reserved_run,
    run_row,
    spend,
)
from tests.gateway_support import MetricDelta, metric_delta

KIND = "llm_judge"
OUTCOMES = ("done", "failed", "skipped", ABANDONED, BUDGET_EXCEEDED)
ELEVEN_MINUTES = timedelta(minutes=11)


def _reap_only(settings: Settings) -> Settings:
    """Settings under which `sample_all` reaps and samples nothing."""
    return settings.model_copy(update={"evals_enabled": False})


async def test_concurrent_reservations_serialize_under_the_lock(
    session_factory: Sessions,
) -> None:
    now = datetime.now(UTC)
    project_id, evaluator_id = await judge_evaluator(session_factory)
    runs = [await insert_run(session_factory, evaluator_id, started_at=now) for _ in range(10)]
    estimate, budget = Decimal("0.3"), Decimal("1.0")

    callers = [
        reserve(session_factory, project_id, run_id, estimate=estimate, budget=budget, now=now)
        for run_id in runs
    ]
    admitted = await asyncio.gather(*callers)

    assert sum(admitted) == 3
    rows = [await run_row(session_factory, run_id) for run_id in runs]
    running = [row for row in rows if row["status"] == "running"]
    refused = [row for row in rows if row["status"] == "failed"]
    assert len(running) == 3 and len(refused) == 7
    assert all(row["reserved_usd"] == estimate for row in running)
    assert all(
        (row["error"], row["cost_usd"], row["reserved_usd"]) == (BUDGET_EXCEEDED, 0, 0)
        for row in refused
    )
    assert (await spend(session_factory, evaluator_id, now)).committed == Decimal("0.9")

    # One run reserved twice at once, with room for both: the second caller read the run as
    # queued before the lock, so only the update's row count tells it the run is taken.
    twice = await insert_run(session_factory, evaluator_id, started_at=now)
    both = [
        reserve(session_factory, project_id, twice, estimate=estimate, budget=Decimal(10), now=now)
        for _ in range(2)
    ]
    assert sorted(await asyncio.gather(*both)) == [False, True]
    assert (await spend(session_factory, evaluator_id, now)).committed == Decimal("1.2")


def test_can_spend_boundary() -> None:
    budget = Decimal("0.0025")
    assert can_spend(Decimal("0.0024"), Decimal("0.0001"), budget)
    assert not can_spend(Decimal("0.0025"), Decimal("0.0001"), budget)
    # ceil(ascii / 3) + one token per other character.
    assert estimate_text_tokens("") == 0
    assert estimate_text_tokens("abc") == 1
    assert estimate_text_tokens("abcd") == 2
    assert estimate_text_tokens("abcdéé") == 4
    assert estimate_text_tokens("日本語") == 3


async def test_settle_below_estimate_returns_the_slack(session_factory: Sessions) -> None:
    now = datetime.now(UTC)
    project_id, evaluator_id = await judge_evaluator(session_factory)
    budget = Decimal("1.0")
    first = await insert_run(session_factory, evaluator_id, started_at=now)
    assert await reserve(
        session_factory, project_id, first, estimate=Decimal("0.3"), budget=budget, now=now
    )
    assert (await spend(session_factory, evaluator_id, now)).committed == Decimal("0.3")

    async with session_factory() as db, db.begin():
        await bind_project(db, project_id)
        settled = await settle_run(
            db, first, succeeded=True, cost_usd=Decimal("0.1"), error=None, now=now
        )

    assert settled
    month = await spend(session_factory, evaluator_id, now)
    assert (month.spent, month.reserved, month.committed) == (
        Decimal("0.1"),
        Decimal(0),
        Decimal("0.1"),
    )
    row = await run_row(session_factory, first)
    assert (row["status"], row["cost_usd"], row["reserved_usd"]) == ("done", Decimal("0.1"), 0)
    # 0.1 + 0.9 fits exactly; with the whole 0.3 still held it would not have.
    second = await insert_run(session_factory, evaluator_id, started_at=now)
    assert await reserve(
        session_factory, project_id, second, estimate=Decimal("0.9"), budget=budget, now=now
    )


async def test_reaper_fails_stale_runs_and_charges_their_reservation(
    session_factory: Sessions, settings: Settings
) -> None:
    now = datetime.now(UTC)
    _, evaluator_id = await judge_evaluator(session_factory)
    runs = await reaper_world(session_factory, evaluator_id, now)
    alive_before = await run_row(session_factory, runs.alive)
    cost = metric_delta(EVALUATOR_COST, kind=KIND)
    abandoned = metric_delta(EVALUATOR_RUNS, kind=KIND, outcome=ABANDONED)

    run = await sample_all(session_factory, _reap_only(settings), now=now)

    assert run.reaped == 2
    reaped = await run_row(session_factory, runs.stale)
    assert (reaped["status"], reaped["error"]) == ("failed", ABANDONED)
    assert (reaped["cost_usd"], reaped["reserved_usd"]) == (Decimal("0.3"), 0)
    assert reaped["finished_at"] == now
    assert await run_row(session_factory, runs.alive) == alive_before
    # A queued run held no reservation, so it is failed at no charge.
    dead = await run_row(session_factory, runs.orphaned)
    assert (dead["status"], dead["error"], dead["cost_usd"]) == ("failed", ABANDONED, 0)
    assert (await run_row(session_factory, runs.waiting))["status"] == "queued"
    assert abandoned.value == 2
    assert cost.value == pytest.approx(0.3)
    assert (await spend(session_factory, evaluator_id, now)).committed == Decimal("0.6")


def _run_counts() -> dict[str, MetricDelta]:
    return {o: metric_delta(EVALUATOR_RUNS, kind=KIND, outcome=o) for o in OUTCOMES}


def _counted(counts: dict[str, MetricDelta]) -> dict[str, float]:
    """How much each outcome grew, for the outcomes that did."""
    return {outcome: delta.value for outcome, delta in counts.items() if delta.value}


async def _late_settles(deps: RunnerDeps, ref: RunRef) -> list[RunOutcome]:
    """Both ways a run can settle after the fact, each observed as the runner would."""
    outcomes = [
        await settle_success(
            deps, ref, EvalResult(scores=[], cost_usd=Decimal("0.1")), store_scores=True
        ),
        await settle_failure(deps, ref, charge=Decimal("0.3"), error="judge_no_answer"),
    ]
    for outcome in outcomes:
        observe(ref, outcome, time.monotonic())
    return outcomes


async def test_late_settle_after_reaper_is_a_no_op(
    session_factory: Sessions, settings: Settings
) -> None:
    now = datetime.now(UTC)
    project_id, evaluator_id = await judge_evaluator(session_factory)
    run_id = await reserved_run(session_factory, evaluator_id, project_id, now - ELEVEN_MINUTES)
    counts, cost = _run_counts(), metric_delta(EVALUATOR_COST, kind=KIND)
    await sample_all(session_factory, _reap_only(settings), now=now)

    # The worker was only slow: its settles arrive after the reaper failed the run.
    async with session_factory() as db, db.begin():
        await bind_project(db, project_id)
        assert not await settle_run(
            db, run_id, succeeded=True, cost_usd=Decimal("0.1"), error=None, now=now
        )
    deps = RunnerDeps(sessions=session_factory, settings=settings, clock=lambda: now)
    late = await _late_settles(deps, RunRef(run_id, project_id, evaluator_id, KIND, "test"))

    assert late == [RunOutcome("failed", error=ABANDONED)] * 2
    row = await run_row(session_factory, run_id)
    assert (row["status"], row["error"]) == ("failed", ABANDONED)
    assert (row["cost_usd"], row["reserved_usd"]) == (Decimal("0.3"), 0)
    assert (await spend(session_factory, evaluator_id, now)).committed == Decimal("0.3")
    assert _counted(counts) == {ABANDONED: 1}
    assert cost.value == pytest.approx(0.3)


async def test_settle_of_a_deleted_run_is_counted_once(
    session_factory: Sessions, settings: Settings
) -> None:
    now = datetime.now(UTC)
    project_id, evaluator_id = await judge_evaluator(session_factory)
    run_id = await reserved_run(session_factory, evaluator_id, project_id, now)
    counts, cost = _run_counts(), metric_delta(EVALUATOR_COST, kind=KIND)
    # The judge is deleted while its call is out; its runs go with it, and no reaper sees them.
    await delete_evaluator(session_factory, evaluator_id)

    deps = RunnerDeps(sessions=session_factory, settings=settings, clock=lambda: now)
    late = await _late_settles(deps, RunRef(run_id, project_id, evaluator_id, KIND, "test"))

    assert late == [
        RunOutcome("failed", Decimal("0.1"), RUN_DELETED),
        RunOutcome("failed", Decimal("0.3"), RUN_DELETED),
    ]
    assert _counted(counts) == {"failed": 2}
    assert cost.value == pytest.approx(0.4)
