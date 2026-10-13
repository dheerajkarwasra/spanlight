"""How a run ends: its row settled, its scores stored, its outcome counted and logged.

Each step runs in a session of its own from `RunnerDeps.sessions`, bound to the run's project, so
it does not depend on a request session that may be gone. A successful run's settlement and its
scores commit together: a retried heuristic run never stores its scores twice, and a judge run
that the reaper already failed (its worker was presumed dead) stores none.

Scores go through `app.scores.service.write_scores` with source `eval` and the run's id. A score
the write path rejects (a type that disagrees with the score's config, say) is counted in the log;
the run is still `done`. Logs carry ids, codes, counts and costs, never payloads or reasoning.
"""

import time
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.observability import EVALUATOR_COST, EVALUATOR_RUNS
from app.db.rls import bind_project
from app.evals.reservation import BUDGET_EXCEEDED, settle_run
from app.evals.run_queries import fail_unreserved_run, run_status
from app.evals.runner_build import RunnerDeps
from app.evals.schemas import EvalResult
from app.scores.schemas import ScoreIn
from app.scores.service import write_scores
from app.scores.types import ScoreSource

logger = structlog.get_logger(__name__)

EVALUATOR_DISABLED = "evaluator_disabled"
ABANDONED = "abandoned"
# The run row was deleted while it ran (its evaluator or project was deleted).
RUN_DELETED = "run_deleted"

RunStatus = Literal["done", "failed", "skipped"]


@dataclass(frozen=True, slots=True)
class RunRef:
    """Which run this is, for its writes and its log line."""

    run_id: uuid.UUID
    project_id: uuid.UUID
    evaluator_id: uuid.UUID
    kind: str
    source: str


@dataclass(frozen=True, slots=True)
class RunOutcome:
    """How a run ended. `skipped`: nothing was done (the run had already started or ended)."""

    status: RunStatus
    cost_usd: Decimal = Decimal(0)
    error: str | None = None
    scores: tuple[ScoreIn, ...] = ()
    reasoning: str | None = None

    @property
    def label(self) -> str:
        """The `outcome` label of `spanlight_evaluator_runs_total`."""
        if self.status == "failed" and self.error in (BUDGET_EXCEEDED, EVALUATOR_DISABLED):
            return self.error
        return self.status


async def settle_success(
    deps: RunnerDeps, ref: RunRef, result: EvalResult, *, store_scores: bool
) -> RunOutcome:
    """End the run `done`, charged `result.cost_usd`, and store its scores with it."""
    async with deps.sessions() as db, db.begin():
        await bind_project(db, ref.project_id)
        settled = await settle_run(
            db, ref.run_id, succeeded=True, cost_usd=result.cost_usd, error=None, now=deps.clock()
        )
        if not settled:
            return await _unsettled(db, ref, result.cost_usd)
        if store_scores and result.scores:
            written = await write_scores(
                db,
                ref.project_id,
                result.scores,
                source=ScoreSource.EVAL,
                evaluator_run_id=ref.run_id,
            )
            if written.rejected:
                logger.warning(
                    "evaluator_scores_rejected",
                    run_id=str(ref.run_id),
                    evaluator_id=str(ref.evaluator_id),
                    rejected=len(written.rejected),
                )
    return RunOutcome(
        "done", result.cost_usd, scores=tuple(result.scores), reasoning=result.reasoning
    )


async def settle_failure(
    deps: RunnerDeps, ref: RunRef, *, charge: Decimal, error: str
) -> RunOutcome:
    """End a running run `failed`, charged `charge`, and release its reservation."""
    async with deps.sessions() as db, db.begin():
        await bind_project(db, ref.project_id)
        settled = await settle_run(
            db, ref.run_id, succeeded=False, cost_usd=charge, error=error, now=deps.clock()
        )
        if not settled:
            return await _unsettled(db, ref, charge)
    return RunOutcome("failed", charge, error)


async def _unsettled(db: AsyncSession, ref: RunRef, charge: Decimal) -> RunOutcome:
    """The outcome of a run that was no longer running when it came to settle. Either it ended
    without this attempt (the reaper failed it, charging its reservation, or another attempt
    settled it): `abandoned`, counted there. Or its row is gone, deleted with its evaluator or
    project while the call was out: `run_deleted`, failed with what it would have been charged,
    since nothing else counts it."""
    if await run_status(db, ref.run_id) is None:
        return RunOutcome("failed", charge, RUN_DELETED)
    return RunOutcome("failed", error=ABANDONED)


async def fail_unstarted(deps: RunnerDeps, ref: RunRef, error: str) -> RunOutcome:
    """End a run that holds no reservation `failed`, at no charge."""
    async with deps.sessions() as db, db.begin():
        await bind_project(db, ref.project_id)
        failed = await fail_unreserved_run(db, ref.run_id, error, deps.clock())
    return RunOutcome("failed", error=error) if failed else RunOutcome("skipped")


def observe(ref: RunRef, outcome: RunOutcome, started: float) -> None:
    """Count the run and log `evaluator_run_finished`. An `abandoned` run ended without this
    attempt and was counted (and charged) where it ended, so it is only logged here."""
    if outcome.error != ABANDONED:
        EVALUATOR_RUNS.labels(kind=ref.kind, outcome=outcome.label).inc()
    if outcome.cost_usd > 0:
        EVALUATOR_COST.labels(kind=ref.kind).inc(float(outcome.cost_usd))
    logger.info(
        "evaluator_run_finished",
        run_id=str(ref.run_id),
        project_id=str(ref.project_id),
        evaluator_id=str(ref.evaluator_id),
        kind=ref.kind,
        source=ref.source,
        status=outcome.status,
        error=outcome.error,
        cost_usd=str(outcome.cost_usd),
        scores=len(outcome.scores),
        duration_ms=round((time.monotonic() - started) * 1000),
    )
