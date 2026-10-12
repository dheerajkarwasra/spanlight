"""Run evaluators: a sampled run from the worker, an experiment item, or a test from the dashboard.

Every evaluation is an `evaluator_runs` row, so a judge's spend always counts against its
evaluator's monthly budget: `online` rows come from the sampler, `experiment` and `test` rows are
inserted here. A run is prepared in one transaction bound to the project (the run, its evaluator,
the target span, the scorer), then evaluated with no transaction open:

* heuristic and webhook runs cost nothing and hold no reservation: `queued` → `running` →
  `done` with their scores, or `failed` with the evaluator's error code;
* judge runs go through the reservation of `app.evals.runner_judge`.

A disabled evaluator fails its queued online run with `evaluator_disabled`; a deleted one took its
runs with it. A heuristic run may be retried by its job after an infrastructure failure, so it
resumes a run left `running`; judge and webhook jobs have one attempt. Errors are stable codes;
an unexpected exception is `internal_error` (its message could carry a payload or a URL).
"""

import time
import uuid
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import FieldError, ProblemError, not_configured, not_found
from app.db.models import (
    Evaluator,
    EvaluatorKind,
    EvaluatorRun,
    EvaluatorRunSource,
    EvaluatorRunStatus,
    Project,
    Trace,
)
from app.db.rls import bind_project
from app.evals import run_queries
from app.evals.evaluators.base import EvaluatorError
from app.evals.evaluators.judge import JudgeEvaluator
from app.evals.reservation import BUDGET_EXCEEDED
from app.evals.run_settle import (
    EVALUATOR_DISABLED,
    RunOutcome,
    RunRef,
    observe,
    settle_failure,
    settle_success,
)
from app.evals.runner_build import AnyEvaluator, RunnerDeps, build_eval_input, build_evaluator
from app.evals.runner_judge import INTERNAL_ERROR, run_judge
from app.evals.schemas import EvalInput, EvalResult

logger = structlog.get_logger(__name__)

TRACE_NOT_FOUND = "trace_not_found"
NO_PAYLOAD = "no_payload"
_NOT_CONFIGURED = {
    "gateway_not_configured": "The gateway runtime is not running in this process.",
    "credentials_not_configured": "Set CREDENTIALS_KEYS so provider credentials can be used.",
}


@dataclass(frozen=True)
class Prepared:
    ref: RunRef
    evaluator: AnyEvaluator
    input: EvalInput
    budget: Decimal | None


async def run(deps: RunnerDeps, run_id: uuid.UUID, project_id: uuid.UUID) -> RunOutcome:
    """Evaluate one queued run of the project (the `run_evaluator` job) and store its scores."""
    started = time.monotonic()
    async with AsyncExitStack() as stack:
        async with deps.sessions() as db, db.begin():
            await bind_project(db, project_id)
            loaded = await run_queries.load_run(db, run_id)
            if loaded is None:
                logger.info("evaluator_run_gone", run_id=str(run_id), project_id=str(project_id))
                return RunOutcome("skipped")
            row, evaluator = loaded
            ref = RunRef(run_id, project_id, evaluator.id, evaluator.kind, row.source)
            step = await _prepare(db, deps, ref, row, evaluator, stack)
        if isinstance(step, Prepared):
            outcome = await evaluate_run(deps, step, store_scores=True)
        else:
            outcome = step
    observe(ref, outcome, started)
    return outcome


async def _prepare(
    db: AsyncSession,
    deps: RunnerDeps,
    ref: RunRef,
    row: EvaluatorRun,
    evaluator: Evaluator,
    stack: AsyncExitStack,
) -> Prepared | RunOutcome:
    """The run ready to evaluate, or how it ended without evaluating (in `db`'s transaction)."""
    runnable = {EvaluatorRunStatus.QUEUED.value}
    if evaluator.kind == EvaluatorKind.HEURISTIC:
        runnable.add(EvaluatorRunStatus.RUNNING.value)
    if row.status not in runnable:
        return RunOutcome("skipped")
    if not evaluator.enabled:
        return await _fail(db, deps, ref, EVALUATOR_DISABLED)
    project = await db.get(Project, ref.project_id)
    target = await run_queries.load_target(db, ref.project_id, row.trace_id, row.span_id)
    trace, span = target if target is not None else (None, None)
    if project is None or trace is None or span is None:
        return await _fail(db, deps, ref, TRACE_NOT_FOUND)
    if span.input is None and span.output is None:
        return await _fail(db, deps, ref, NO_PAYLOAD)
    try:
        scorer = await build_evaluator(db, deps, project, evaluator, stack)
    except EvaluatorError as error:
        return await _fail(db, deps, ref, error.code)
    eval_input = build_eval_input(trace, span, run_id=ref.run_id)
    return Prepared(ref, scorer, eval_input, evaluator.monthly_budget_usd)


async def _fail(db: AsyncSession, deps: RunnerDeps, ref: RunRef, error: str) -> RunOutcome:
    failed = await run_queries.fail_unreserved_run(db, ref.run_id, error, deps.clock())
    return RunOutcome("failed", error=error) if failed else RunOutcome("skipped")


async def evaluate_run(deps: RunnerDeps, prepared: Prepared, *, store_scores: bool) -> RunOutcome:
    """Evaluate a prepared, queued run and settle it."""
    if isinstance(prepared.evaluator, JudgeEvaluator):
        return await run_judge(
            deps,
            prepared.ref,
            prepared.evaluator,
            prepared.input,
            prepared.budget,
            store_scores=store_scores,
        )
    ref = prepared.ref
    async with deps.sessions() as db, db.begin():
        await bind_project(db, ref.project_id)
        resume = ref.kind == EvaluatorKind.HEURISTIC
        started = await run_queries.start_run(db, ref.run_id, deps.clock(), resume=resume)
    if not started:
        return RunOutcome("skipped")
    try:
        result = await prepared.evaluator.evaluate(prepared.input)
    except EvaluatorError as error:
        return await settle_failure(deps, ref, charge=Decimal(0), error=error.code)
    except Exception as error:  # noqa: BLE001 - one evaluator's defect fails its run only
        logger.error(
            "evaluator_failed",
            run_id=str(ref.run_id),
            evaluator_id=str(ref.evaluator_id),
            error_type=type(error).__name__,
        )
        return await settle_failure(deps, ref, charge=Decimal(0), error=INTERNAL_ERROR)
    return await settle_success(deps, ref, result, store_scores=store_scores)


async def evaluate_item(
    deps: RunnerDeps,
    project: Project,
    evaluator: Evaluator,
    *,
    trace_id: str,
    span_id: str | None,
    input: Any,
    output: Any,
    expected_output: Any | None,
    metadata: dict[str, Any] | None = None,
) -> RunOutcome:
    """Score one experiment item's call (its trace in `project`) under the evaluator's budget.

    Writes an `experiment` run and, when it succeeds, the scores on the item's trace (source
    `eval`). A refused budget or a failure is an outcome, never an exception.
    """
    started = time.monotonic()
    async with AsyncExitStack() as stack:
        async with deps.sessions() as db, db.begin():
            await bind_project(db, project.id)
            run_id = await run_queries.insert_run(
                db,
                evaluator.id,
                trace_id=trace_id,
                span_id=span_id,
                source=EvaluatorRunSource.EXPERIMENT,
                now=deps.clock(),
            )
            ref = RunRef(run_id, project.id, evaluator.id, evaluator.kind, "experiment")
            eval_input = EvalInput(
                trace_id=trace_id,
                span_id=span_id,
                input=input,
                output=output,
                expected_output=expected_output,
                metadata={**(metadata or {}), "run_id": str(run_id)},
            )
            try:
                scorer = await build_evaluator(db, deps, project, evaluator, stack)
                step: Prepared | RunOutcome = Prepared(
                    ref, scorer, eval_input, evaluator.monthly_budget_usd
                )
            except EvaluatorError as error:
                step = await _fail(db, deps, ref, error.code)
        if isinstance(step, Prepared):
            outcome = await evaluate_run(deps, step, store_scores=True)
        else:
            outcome = step
    observe(ref, outcome, started)
    return outcome


async def test_evaluator(
    db: AsyncSession,
    deps: RunnerDeps,
    project: Project,
    evaluator: Evaluator,
    trace_id: str,
    *,
    now: datetime,
) -> EvalResult:
    """Evaluate the trace's last LLM span now and return the scores without storing them.

    `db` is the request's session, bound to the project; it is committed (the `test` run row)
    before any call. Raises `ProblemError`: 404 for a trace not in the project, 422 for one with
    nothing to evaluate, 409 `NOT_CONFIGURED`, 402 `EVALUATOR_BUDGET_EXCEEDED` and 502
    `EVALUATOR_FAILED` (the run's error code in `error`).
    """
    started = time.monotonic()
    trace = await db.get(Trace, (project.id, trace_id))
    if trace is None:
        raise not_found("No trace with this id in the project.")
    span = await run_queries.last_llm_span(db, project.id, trace_id)
    if span is None or (span.input is None and span.output is None):
        raise _unevaluable(span is None)
    async with AsyncExitStack() as stack:
        try:
            scorer = await build_evaluator(db, deps, project, evaluator, stack)
        except EvaluatorError as error:
            raise _problem(error.code) from None
        run_id = await run_queries.insert_run(
            db,
            evaluator.id,
            trace_id=trace_id,
            span_id=span.span_id,
            source=EvaluatorRunSource.TEST,
            now=now,
        )
        ref = RunRef(run_id, project.id, evaluator.id, evaluator.kind, "test")
        prepared = Prepared(
            ref, scorer, build_eval_input(trace, span, run_id=run_id), evaluator.monthly_budget_usd
        )
        await db.commit()  # the run must be visible to the reservation; no connection is held
        outcome = await evaluate_run(deps, prepared, store_scores=False)
    observe(ref, outcome, started)
    if outcome.status != "done":
        raise _problem(outcome.error or "run_not_started")
    return EvalResult(
        scores=list(outcome.scores), cost_usd=outcome.cost_usd, reasoning=outcome.reasoning
    )


def _unevaluable(no_span: bool) -> ProblemError:
    message = (
        "The trace has no LLM span to evaluate."
        if no_span
        else "The trace's last LLM span has no stored input or output."
    )
    return ProblemError(
        422, "VALIDATION_ERROR", message, errors=[FieldError(field="trace_id", message=message)]
    )


def _problem(code: str) -> ProblemError:
    if code in _NOT_CONFIGURED:
        return not_configured(_NOT_CONFIGURED[code])
    if code == BUDGET_EXCEEDED:
        return ProblemError(
            402,
            "EVALUATOR_BUDGET_EXCEEDED",
            "This evaluator's monthly budget cannot cover another call this month.",
        )
    return ProblemError(
        502,
        "EVALUATOR_FAILED",
        f"The evaluator could not score the trace ({code}).",
        extensions={"error": code},
    )
