"""The `run_experiment` job: replay a dataset through an experiment's target and score it.

Paid, one attempt, in the worker's long-job slot. The run starts (`experiment_prepare.start`),
then its items are read in id order, a page at a time, up to `max_items`, with four calls in
flight. Before each call is dispatched the cost cap must hold:

    accumulated cost + the estimates of the calls in flight + this call's estimate ≤ max_cost_usd

where an estimate is the price book's ceiling for the compiled prompt and the output-token limit
(`app.evals.budget.estimate_call_cost`) and a finished item counts its real cost (its estimate
when the cost is unknown). When the cap would be passed, the calls in flight finish and the check
is repeated with their real costs; if it still fails, no further item runs and the run ends
`failed` with `cost_cap_reached`. The stored items stay. The attached evaluators spend under their
own monthly budgets, not under this cap.

The job extends its lease and the run's `heartbeat_at` before every call and every 20 seconds.
The run ends `done`, or `failed` with its error; its totals are computed from the stored items.
A cancelled job (worker shutdown, timeout) ends the run `cancelled` and a lost lease
`lease_lost`; any other failure `internal_error`; the calls in flight are cancelled and store
their items first. A run whose worker died is failed `abandoned` by the reaper
(`experiment_queries.reap_abandoned_runs`); a heartbeat that finds the run no longer running stops
the dispatch, so a reaped run never starts another paid call.
"""

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from decimal import Decimal
from typing import Any

import structlog

from app.budgets.guard import get_budget_guard
from app.datasets import service as datasets
from app.db.models import DatasetItem, ExperimentRunStatus
from app.db.rls import bind_project
from app.evals import experiment_queries as queries
from app.evals.experiment_item import ItemDone, RunPlan, run_item
from app.evals.experiment_prepare import start
from app.evals.experiment_request import item_request, run_tag
from app.evals.experiment_totals import summarize_items, totals_json
from app.evals.jobs import EvalServices
from app.evals.runner_build import RunnerDeps
from app.gateway.http import build_http_client
from app.gateway.settle_helpers import shielded
from app.jobs.context import TaskContext
from app.jobs.outcome import JobOutcome
from app.jobs.queue import LeaseLostError
from app.scores.queries import boolean_score_names

logger = structlog.get_logger(__name__)

CONCURRENCY = 4
PAGE_SIZE = 20
TICK_SECONDS = 20.0
COST_CAP_REACHED = "cost_cap_reached"
MODEL_UNPRICED = "model_unpriced"

InFlight = dict["asyncio.Task[ItemDone]", Decimal]


async def run_run_experiment(context: TaskContext, payload: dict[str, Any]) -> JobOutcome | None:
    run_id = uuid.UUID(str(payload["run_id"]))
    project_id = uuid.UUID(str(payload["project_id"]))
    settings = context.settings
    async with AsyncExitStack() as stack:
        http = build_http_client(settings)
        stack.push_async_callback(http.aclose)
        services = EvalServices(
            http=http,
            sessions=context.session_factory,
            settings=settings,
            budget_guard=get_budget_guard(settings),
        )
        deps = RunnerDeps(
            sessions=context.session_factory,
            settings=settings,
            services=services,
            heartbeat=context.heartbeat,
        )
        await execute_run(deps, run_id, project_id)
    return None


async def execute_run(deps: RunnerDeps, run_id: uuid.UUID, project_id: uuid.UUID) -> None:
    started = await start(deps, run_id, project_id)
    if started is None:
        return
    plan = started.plan
    ticker = asyncio.create_task(_tick(deps, plan), name=f"experiment-heartbeat-{run_id}")
    try:
        error = await _dispatch(deps, plan, started.dataset_id, started.max_items)
    except BaseException as failure:
        await shielded(_finish(deps, plan, _failure_code(failure)))
        raise
    finally:
        ticker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await ticker
    await shielded(_finish(deps, plan, error))


class RunEndedError(Exception):
    """The run is no longer `running` (the reaper ended it); no further item may start."""


def _failure_code(failure: BaseException) -> str:
    if isinstance(failure, asyncio.CancelledError):
        return "cancelled"
    if isinstance(failure, LeaseLostError):
        return "lease_lost"
    if isinstance(failure, RunEndedError):
        return queries.ABANDONED
    logger.error("experiment_run_failed", error_type=type(failure).__name__)
    return "internal_error"


class _Ledger:
    """The calls in flight with their estimates, and what the finished ones cost. Only the
    dispatching coroutine touches it, so a check followed by a dispatch cannot interleave."""

    def __init__(self, cap: Decimal) -> None:
        self.cap = cap
        self.in_flight: InFlight = {}
        self.spent = Decimal(0)

    def fits(self, estimate: Decimal) -> bool:
        return self.spent + sum(self.in_flight.values(), Decimal(0)) + estimate <= self.cap

    async def admit(self, estimate: Decimal) -> bool:
        """Whether a call of `estimate` fits; when not at first, after the calls in flight have
        finished and count their real cost."""
        if self.fits(estimate):
            return True
        await self.drain(below=1)
        return self.fits(estimate)

    async def drain(self, *, below: int) -> None:
        """Wait until fewer than `below` items are in flight. A finished item counts its cost,
        or its estimate when the cost is unknown."""
        while self.in_flight and len(self.in_flight) >= below:
            done, _ = await asyncio.wait(self.in_flight, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                estimate = self.in_flight.pop(task)
                finished = task.result()
                self.spent += finished.cost_usd if finished.cost_usd is not None else estimate

    async def cancel_all(self) -> None:
        """Cancel the calls in flight and wait for them (each stores its item first)."""
        for task in self.in_flight:
            task.cancel()
        await asyncio.gather(*self.in_flight, return_exceptions=True)


async def _dispatch(
    deps: RunnerDeps, plan: RunPlan, dataset_id: uuid.UUID, max_items: int
) -> str | None:
    """Run the items under the cost cap; returns the run's error, None when every item ran."""
    ledger = _Ledger(plan.max_cost_usd)
    try:
        async for item in _items(deps, plan, dataset_id, max_items):
            error = await _dispatch_one(deps, plan, ledger, item)
            if error is not None:
                await ledger.drain(below=1)
                return error
        await ledger.drain(below=1)
        return None
    except BaseException:
        await ledger.cancel_all()
        raise


async def _dispatch_one(
    deps: RunnerDeps, plan: RunPlan, ledger: _Ledger, item: DatasetItem
) -> str | None:
    """Start one item when a slot is free and the cap allows; returns the run's error if not."""
    at = deps.clock()
    prepared = item_request(
        plan.target,
        plan.version,
        plan.credential.provider,
        item.input,
        run_tag=run_tag(plan.run_id),
        prices=plan.prices,
        at=at,
    )
    await ledger.drain(below=CONCURRENCY)
    estimate = Decimal(0)
    if prepared.request is not None:
        if prepared.estimate is None:
            return MODEL_UNPRICED
        estimate = prepared.estimate
        if not await ledger.admit(estimate):
            return COST_CAP_REACHED
        await _beat(deps, plan)
    task = asyncio.create_task(run_item(deps, plan, item, prepared, at=at))
    ledger.in_flight[task] = estimate
    return None


async def _items(
    deps: RunnerDeps, plan: RunPlan, dataset_id: uuid.UUID, max_items: int
) -> AsyncIterator[DatasetItem]:
    """The dataset's first `max_items` items in id order, read a page at a time."""
    read, after = 0, None
    while read < max_items:
        page = await _page(deps, plan, dataset_id, after, min(PAGE_SIZE, max_items - read))
        if not page:
            return
        for item in page:
            read, after = read + 1, item.id
            yield item


async def _page(
    deps: RunnerDeps,
    plan: RunPlan,
    dataset_id: uuid.UUID,
    after: uuid.UUID | None,
    limit: int,
) -> list[DatasetItem]:
    async with deps.sessions() as db, db.begin():
        await bind_project(db, plan.project.id)
        return await datasets.item_page(db, dataset_id, after=after, limit=limit)


async def _beat(deps: RunnerDeps, plan: RunPlan) -> None:
    """Extend the job's lease (raises `LeaseLostError`) and the run's heartbeat (raises
    `RunEndedError` when the run is no longer running)."""
    if deps.heartbeat is not None:
        await deps.heartbeat()
    async with deps.sessions() as db, db.begin():
        await bind_project(db, plan.project.id)
        alive = await queries.touch_run(db, plan.run_id, deps.clock())
    if not alive:
        raise RunEndedError


async def _tick(deps: RunnerDeps, plan: RunPlan) -> None:
    """Keep the run alive while slow calls are in flight; a failed beat is retried next tick."""
    while True:
        await asyncio.sleep(TICK_SECONDS)
        try:
            await _beat(deps, plan)
        except RunEndedError:
            return  # the next item's beat stops the dispatch
        except Exception as error:  # noqa: BLE001 - the next tick or item beat tries again
            logger.warning(
                "experiment_heartbeat_failed",
                run_id=str(plan.run_id),
                error_type=type(error).__name__,
            )


async def _finish(deps: RunnerDeps, plan: RunPlan, error: str | None) -> None:
    status = ExperimentRunStatus.DONE if error is None else ExperimentRunStatus.FAILED
    async with deps.sessions() as db, db.begin():
        await bind_project(db, plan.project.id)
        totals = summarize_items(
            await queries.item_facts(db, plan.run_id),
            boolean_scores=await boolean_score_names(db, plan.project.id),
        )
        ended = await queries.finish_run(
            db,
            plan.run_id,
            status=status,
            error=error,
            totals=totals_json(totals),
            now=deps.clock(),
        )
    logger.info(
        "experiment_run_finished",
        run_id=str(plan.run_id),
        experiment_id=str(plan.experiment_id),
        project_id=str(plan.project.id),
        status=status.value if ended else "already_ended",
        error=error,
        items=totals.items,
        errors=totals.errors,
        cost_usd=None if totals.cost_usd is None else str(totals.cost_usd),
    )
