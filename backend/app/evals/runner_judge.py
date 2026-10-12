"""A judge run under its evaluator's monthly budget (ADR 0015): reserve, call, settle.

1. `prepare` compiles the judge's prompt and estimates the call's cost; a template, provider or
   price problem fails the run at no charge, before anything is reserved.
2. The worker's lease is extended (the call is paid), then, in one short committed transaction,
   `reserve_run` admits the run holding the estimate, or fails it with `budget_exceeded` at no
   charge and no provider request.
3. The call goes out with no transaction open.
4. The run settles in a session of its own, shielded from cancellation: `done` with the call's
   cost and the verdict's score; `failed` with what `JudgeCallError` says it should be charged;
   `failed` at the estimate when the call raised anything else or was cancelled, since the
   provider may have been paid.
"""

from decimal import Decimal

import structlog

from app.db.rls import bind_project
from app.evals.evaluators.base import EvaluatorError
from app.evals.evaluators.judge import JudgeCall, JudgeCallError, JudgeEvaluator
from app.evals.reservation import BUDGET_EXCEEDED, reserve_run
from app.evals.run_queries import run_status
from app.evals.run_settle import (
    RunOutcome,
    RunRef,
    fail_unstarted,
    settle_failure,
    settle_success,
)
from app.evals.runner_build import RunnerDeps
from app.evals.schemas import EvalInput
from app.gateway.settle_helpers import shielded

logger = structlog.get_logger(__name__)

CANCELLED = "cancelled"
INTERNAL_ERROR = "internal_error"


async def run_judge(
    deps: RunnerDeps,
    ref: RunRef,
    judge: JudgeEvaluator,
    input: EvalInput,
    budget: Decimal | None,
    *,
    store_scores: bool,
) -> RunOutcome:
    """Evaluate one queued judge run (see the module docstring)."""
    if budget is None:  # the table's CHECK forbids it; never call a judge without a ceiling
        return await fail_unstarted(deps, ref, "budget_required")
    try:
        call = judge.prepare(input)
    except EvaluatorError as error:
        return await fail_unstarted(deps, ref, error.code)
    if deps.heartbeat is not None:
        await deps.heartbeat()
    refused = await _reserve(deps, ref, call, budget)
    if refused is not None:
        return refused
    try:
        result = await judge.call(call)
    except JudgeCallError as error:
        return await shielded(settle_failure(deps, ref, charge=error.charge, error=error.code))
    except Exception as error:  # noqa: BLE001 - the provider may have been paid; charge the estimate
        logger.error(
            "evaluator_judge_call_failed",
            run_id=str(ref.run_id),
            evaluator_id=str(ref.evaluator_id),
            error_type=type(error).__name__,
        )
        return await shielded(settle_failure(deps, ref, charge=call.estimate, error=INTERNAL_ERROR))
    except BaseException:
        await shielded(settle_failure(deps, ref, charge=call.estimate, error=CANCELLED))
        raise
    return await shielded(settle_success(deps, ref, result, store_scores=store_scores))


async def _reserve(
    deps: RunnerDeps, ref: RunRef, call: JudgeCall, budget: Decimal
) -> RunOutcome | None:
    """Reserve the estimate and commit; the outcome when the run was refused, else None."""
    async with deps.sessions() as db, db.begin():
        await bind_project(db, ref.project_id)
        if await reserve_run(db, ref.run_id, call.estimate, budget, deps.clock()):
            return None
        state = await run_status(db, ref.run_id)
    if state is not None and state == ("failed", BUDGET_EXCEEDED):
        return RunOutcome("failed", error=BUDGET_EXCEEDED)
    return RunOutcome("skipped")  # no longer queued: another attempt has it, or it ended
