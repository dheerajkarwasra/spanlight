"""An evaluator's runs, its month-to-date budget, and a synchronous test run.

Members read runs and the budget (`project:read`). A test run (`evaluators:write`) evaluates one
stored trace now and returns the scores without storing them. It is recorded as a `test` run, so
a judge's test call goes through the same budget reservation as a sampled run and testing cannot
overspend.
"""

import uuid
from typing import Annotated, Literal

import structlog
from fastapi import APIRouter, Depends, Query, Request

from app.api.deps import Access, ClockDep, DbSession, require
from app.api.schemas.common import Page
from app.api.schemas.evaluators import (
    EvalResultOut,
    EvaluatorBudgetStatusOut,
    EvaluatorRunOut,
    EvaluatorTestIn,
)
from app.core.errors import not_found
from app.core.pagination import PageLimit, decode_uuid_cursor, encode_cursor
from app.core.permissions import Permission
from app.db.models import Evaluator
from app.evals import queries, service
from app.evals.runner import test_evaluator as run_test
from app.evals.runner_build import RunnerDeps
from app.gateway.context import GatewayServices
from app.scores import service as score_service

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/projects/{project_id}/evaluators", tags=["evaluators"])

EvaluatorReader = Annotated[Access, Depends(require(Permission.PROJECT_READ))]
EvaluatorWriter = Annotated[Access, Depends(require(Permission.EVALUATORS_WRITE))]

DEFAULT_PAGE = 50
RunStatus = Literal["queued", "running", "done", "failed"]


async def _evaluator(db: DbSession, access: Access, evaluator_id: uuid.UUID) -> Evaluator:
    evaluator = await queries.get_evaluator(db, access.require_project().id, evaluator_id)
    if evaluator is None:
        raise not_found()
    return evaluator


@router.get(
    "/{evaluator_id}/runs", response_model=Page[EvaluatorRunOut], summary="List evaluator runs"
)
async def list_runs(
    project_id: uuid.UUID,
    evaluator_id: uuid.UUID,
    access: EvaluatorReader,
    db: DbSession,
    status: RunStatus | None = None,
    limit: PageLimit = DEFAULT_PAGE,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> Page[EvaluatorRunOut]:
    """Newest first; `status` filters. Each run carries the scores it wrote."""
    evaluator = await _evaluator(db, access, evaluator_id)
    after = decode_uuid_cursor(cursor) if cursor else None
    rows = await queries.list_runs(db, evaluator.id, status=status, after=after, limit=limit)
    shown, more = rows[:limit], len(rows) > limit
    last = shown[-1] if shown else None
    scores = await score_service.run_scores(
        db, access.require_project().id, [run.id for run in shown]
    )
    return Page(
        items=[EvaluatorRunOut.of(run, scores.get(run.id, [])) for run in shown],
        next_cursor=encode_cursor(last.started_at, str(last.id)) if more and last else None,
    )


@router.get(
    "/{evaluator_id}/budget",
    response_model=EvaluatorBudgetStatusOut,
    summary="Get an evaluator's budget",
)
async def get_budget(
    project_id: uuid.UUID,
    evaluator_id: uuid.UUID,
    access: EvaluatorReader,
    db: DbSession,
    clock: ClockDep,
) -> EvaluatorBudgetStatusOut:
    """The UTC calendar month so far: settled spend, the reservations of running calls and what
    is left. `monthly_budget_usd` and `remaining_usd` are `null` for an evaluator without one."""
    evaluator = await _evaluator(db, access, evaluator_id)
    return EvaluatorBudgetStatusOut.of(await service.budget_status(db, evaluator, clock()))


@router.post(
    "/{evaluator_id}/test", response_model=EvalResultOut, summary="Test an evaluator on a trace"
)
async def test_evaluator(
    project_id: uuid.UUID,
    evaluator_id: uuid.UUID,
    body: EvaluatorTestIn,
    request: Request,
    access: EvaluatorWriter,
    db: DbSession,
    clock: ClockDep,
) -> EvalResultOut:
    """Evaluate the trace's last LLM span now and return the scores; no score is stored. A
    judge's call is real spend and counts against the evaluator's budget like a sampled run."""
    evaluator = await _evaluator(db, access, evaluator_id)
    services: GatewayServices | None = getattr(request.app.state, "gateway_runtime", None)
    deps = RunnerDeps(
        sessions=request.app.state.session_factory,
        settings=request.app.state.settings,
        services=services,
        clock=clock,
    )
    result = await run_test(
        db, deps, access.require_project(), evaluator, body.trace_id, now=clock()
    )
    logger.info(
        "evaluator_tested",
        org_id=str(access.org.id),
        project_id=str(project_id),
        evaluator_id=str(evaluator_id),
        kind=evaluator.kind,
        scores=len(result.scores),
        cost_usd=str(result.cost_usd),
    )
    return EvalResultOut.of(result)
