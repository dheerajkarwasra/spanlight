"""The online evaluation jobs: `sample_for_evaluation` every minute and one `run_evaluator` per run.

`sample_for_evaluation` (one attempt; the next cycle is the retry) first reaps judge runs whose
worker died (`app.evals.reservation.reap_abandoned_runs`) and experiment runs that can no longer
finish (`app.evals.experiment_queries.reap_abandoned_runs`). It is always scheduled, because test
and experiment runs reserve budget too; only with `EVALS_ENABLED` does it go on, for every enabled
evaluator with a positive sampling rate, to queue a run for each sampled, matching trace that
ended between five minutes and thirty seconds ago (the lag lets a trace finish arriving). The
overlap between cycles is deliberate: a missed cycle recovers, and the partial unique index on
online runs makes a rescan queue nothing twice. At most `EVAL_SAMPLER_MAX_RUNS_PER_CYCLE` runs per
evaluator per cycle (50 by default: `run_evaluator` jobs share the worker's short-job lane with
alerts and rollups). A judge whose month's committed spend already reaches its budget is not
sampled at all, rather than queueing runs that would each fail `budget_exceeded`. Each inserted
run gets one `run_evaluator` job with the dedupe key `run_evaluator:<run_id>`. Listing
evaluators and reaping read every project, with row-level security bypassed; each evaluator's
sampling runs bound to its project.

`run_evaluator` evaluates one run (`app.evals.runner.run`). Judges and webhooks get one attempt:
a retry could pay or post twice. Heuristics get three, since their runs resume safely.
"""

import time
import uuid
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import httpx
import structlog
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.budgets.guard import get_budget_guard
from app.core.ids import new_id
from app.core.observability import EVALUATOR_COST, EVALUATOR_RUNS
from app.db.models import EvaluatorKind
from app.db.rls import bind_project, bypass_rls
from app.evals import experiment_queries, runner
from app.evals.config import EvaluatorFilters
from app.evals.reservation import (
    ABANDONED,
    BUDGET_EXCEEDED,
    RUN_JOB,
    month_spends,
    reap_abandoned_runs,
)
from app.evals.runner_build import RunnerDeps
from app.evals.sampler_queries import (
    SamplingEvaluator,
    candidates,
    insert_online_runs,
    sampling_evaluators,
)
from app.evals.sampling import pick_targets
from app.gateway.budget import BudgetGuard
from app.gateway.cache import GatewayCache, PostgresGatewayCache
from app.gateway.http import build_http_client
from app.jobs.context import TaskContext
from app.jobs.outcome import JobOutcome
from app.jobs.queue import enqueue

if TYPE_CHECKING:
    from app.config import Settings

logger = structlog.get_logger(__name__)

SAMPLE_WINDOW = timedelta(minutes=5)
SETTLE_LAG = timedelta(seconds=30)
# Candidates read per evaluator per cycle, as a multiple of the cap: room for the ones a later
# check leaves out, bounded so one busy project cannot fill the worker's memory.
CANDIDATE_FACTOR = 2
# The worker cancels a task after 50 s; no evaluator starts after 40 s. The evaluators not
# reached are sampled by the next cycle, whose window still covers these traces.
RUN_BUDGET_SECONDS = 40.0


def run_attempts(kind: str) -> int:
    return 3 if kind == EvaluatorKind.HEURISTIC else 1


@dataclass(slots=True)
class SamplerPass:
    reaped: int = 0
    experiment_runs_reaped: int = 0
    evaluators: int = 0
    evaluators_failed: int = 0
    evaluators_left: int = 0
    evaluators_out_of_budget: int = 0
    queued: int = 0


async def run_sample_for_evaluation(context: TaskContext, _: dict[str, Any]) -> None:
    run = await sample_all(
        context.session_factory,
        context.settings,
        now=datetime.now(UTC),
        heartbeat=context.heartbeat,
    )
    logger.info(
        "evaluations_sampled",
        reaped=run.reaped,
        experiment_runs_reaped=run.experiment_runs_reaped,
        evaluators=run.evaluators,
        evaluators_failed=run.evaluators_failed,
        evaluators_left=run.evaluators_left,
        evaluators_out_of_budget=run.evaluators_out_of_budget,
        queued=run.queued,
    )


async def sample_all(
    session_factory: async_sessionmaker[AsyncSession],
    settings: "Settings",
    *,
    now: datetime,
    heartbeat: Callable[[], Awaitable[None]] | None = None,
    budget_seconds: float = RUN_BUDGET_SECONDS,
) -> SamplerPass:
    """Reap abandoned runs, then, with `EVALS_ENABLED`, queue this cycle's runs for every
    sampling evaluator."""
    deadline = time.monotonic() + budget_seconds
    run = SamplerPass()
    async with session_factory() as db, db.begin():
        await bypass_rls(db)
        reaped = await reap_abandoned_runs(db, now)
        run.experiment_runs_reaped = await experiment_queries.reap_abandoned_runs(db, now)
        listed = await sampling_evaluators(db) if settings.evals_enabled else []
        evaluators = await _with_budget_left(db, listed, now)
    run.evaluators_out_of_budget = len(listed) - len(evaluators)
    run.reaped = len(reaped)
    for charged in reaped:
        EVALUATOR_RUNS.labels(kind=charged.kind, outcome=ABANDONED).inc()
        if charged.cost_usd > 0:
            EVALUATOR_COST.labels(kind=charged.kind).inc(float(charged.cost_usd))
    cap = settings.eval_sampler_max_runs_per_cycle
    for index, evaluator in enumerate(evaluators):
        if heartbeat is not None:
            await heartbeat()
        if time.monotonic() >= deadline:
            run.evaluators_left = len(evaluators) - index
            logger.warning("evaluations_sampler_out_of_time", evaluators_left=run.evaluators_left)
            break
        try:
            run.queued += await _sample_one(session_factory, evaluator, cap, now)
            run.evaluators += 1
        except Exception as exc:  # isolate the evaluator; the next cycle tries it again
            run.evaluators_failed += 1
            logger.error(
                "evaluations_sampler_failed",
                evaluator_id=str(evaluator.id),
                project_id=str(evaluator.project_id),
                error_type=type(exc).__name__,
                exc_info=exc,
            )
    return run


async def _with_budget_left(
    db: AsyncSession, evaluators: list[SamplingEvaluator], now: datetime
) -> list[SamplingEvaluator]:
    """The evaluators worth sampling: every one but a judge whose month's committed spend
    already reaches its budget. One grouped spend query for all judges."""
    judges = [e.id for e in evaluators if e.kind == EvaluatorKind.LLM_JUDGE]
    spends = await month_spends(db, judges, now)

    def has_room(evaluator: SamplingEvaluator) -> bool:
        if evaluator.kind != EvaluatorKind.LLM_JUDGE:
            return True
        budget = evaluator.monthly_budget_usd
        spend = spends.get(evaluator.id)
        committed = spend.committed if spend is not None else Decimal(0)
        return budget is not None and committed < budget

    return [evaluator for evaluator in evaluators if has_room(evaluator)]


async def _sample_one(
    session_factory: async_sessionmaker[AsyncSession],
    evaluator: SamplingEvaluator,
    cap: int,
    now: datetime,
) -> int:
    """Queue the evaluator's runs for this cycle; returns how many were queued."""
    try:
        filters = EvaluatorFilters.model_validate(evaluator.filters)
    except ValidationError:
        logger.error("evaluations_sampler_bad_filters", evaluator_id=str(evaluator.id))
        return 0
    async with session_factory() as db, db.begin():
        await bind_project(db, evaluator.project_id)
        rows = await candidates(
            db,
            evaluator,
            filters,
            window_start=now - SAMPLE_WINDOW,
            window_end=now - SETTLE_LAG,
            limit=cap * CANDIDATE_FACTOR,
        )
        picks = pick_targets(evaluator.id, evaluator.sampling_rate, filters, rows, cap)
        targets = [(new_id(), target.trace_id, target.span_id) for target in picks.targets]
        inserted = await insert_online_runs(db, evaluator.id, targets, now)
        for run_id in inserted:
            await enqueue(
                db,
                RUN_JOB,
                {
                    "run_id": str(run_id),
                    "project_id": str(evaluator.project_id),
                    "kind": evaluator.kind,
                },
                dedupe_key=f"{RUN_JOB}:{run_id}",
                max_attempts=run_attempts(evaluator.kind),
            )
    if picks.skipped_cap:
        EVALUATOR_RUNS.labels(kind=evaluator.kind, outcome="skipped_cap").inc(picks.skipped_cap)
    if picks.skipped_no_payload:
        EVALUATOR_RUNS.labels(kind=evaluator.kind, outcome="skipped_no_payload").inc(
            picks.skipped_no_payload
        )
    return len(inserted)


@dataclass(frozen=True)
class EvalServices:
    """The gateway services of one judge run (`GatewayServices`). The worker keeps no
    process-wide resources for its tasks, so each run opens its own upstream client. The
    project's `block` budgets apply to the call through the real budget guard."""

    http: httpx.AsyncClient
    sessions: async_sessionmaker[AsyncSession]
    settings: "Settings"
    budget_guard: BudgetGuard
    cache: GatewayCache = field(default_factory=PostgresGatewayCache)


async def run_run_evaluator(context: TaskContext, payload: dict[str, Any]) -> JobOutcome | None:
    run_id = uuid.UUID(str(payload["run_id"]))
    project_id = uuid.UUID(str(payload["project_id"]))
    settings = context.settings
    async with AsyncExitStack() as stack:
        services = None
        if payload.get("kind") == EvaluatorKind.LLM_JUDGE:
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
        outcome = await runner.run(deps, run_id, project_id)
    return JobOutcome.SKIPPED_BUDGET if outcome.error == BUDGET_EXCEEDED else None
