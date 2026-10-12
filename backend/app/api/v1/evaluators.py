"""Evaluators: automatic scorers of a project's traces.

Members list and read evaluators (`project:read`); admins create, edit and delete them
(`evaluators:write`), since a judge spends the organization's money. Runs, the budget and test
runs are in `evaluator_runs.py`. Responses are built before the commit, which ends the project
binding that row-level security needs.
"""

import uuid
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Query, Request, status

from app.api.deps import Access, Clock, ClockDep, DbSession, require
from app.api.evaluator_problems import EVALUATOR_ERRORS, actor_of, evaluator_problem
from app.api.locks import lock_project_of
from app.api.schemas.common import Page
from app.api.schemas.evaluators import EvaluatorOut, EvaluatorPatch, EvaluatorSpec
from app.core.errors import not_found
from app.core.pagination import PageLimit, decode_uuid_cursor, encode_cursor
from app.core.permissions import Permission
from app.db.models import Evaluator
from app.evals import queries, service

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/projects/{project_id}/evaluators", tags=["evaluators"])

EvaluatorReader = Annotated[Access, Depends(require(Permission.PROJECT_READ))]
EvaluatorWriter = Annotated[Access, Depends(require(Permission.EVALUATORS_WRITE))]

DEFAULT_PAGE = 50


def _log(event: str, access: Access, evaluator_id: uuid.UUID, **fields: object) -> None:
    logger.info(
        event,
        org_id=str(access.org.id),
        project_id=str(access.require_project().id),
        evaluator_id=str(evaluator_id),
        **fields,
    )


async def _out(db: DbSession, evaluator: Evaluator, clock: Clock) -> EvaluatorOut:
    budgets = await service.budget_summaries(db, [evaluator], clock())
    return EvaluatorOut.of(evaluator, budgets.get(evaluator.id))


@router.get("", response_model=Page[EvaluatorOut], summary="List evaluators")
async def list_evaluators(
    project_id: uuid.UUID,
    access: EvaluatorReader,
    db: DbSession,
    clock: ClockDep,
    limit: PageLimit = DEFAULT_PAGE,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> Page[EvaluatorOut]:
    """Newest first. A judge's `budget` carries its month so far."""
    after = decode_uuid_cursor(cursor) if cursor else None
    rows = await queries.list_evaluators(db, access.require_project().id, after=after, limit=limit)
    shown, more = rows[:limit], len(rows) > limit
    last = shown[-1] if shown else None
    budgets = await service.budget_summaries(db, shown, clock())
    return Page(
        items=[EvaluatorOut.of(evaluator, budgets.get(evaluator.id)) for evaluator in shown],
        next_cursor=encode_cursor(last.created_at, str(last.id)) if more and last else None,
    )


@router.post(
    "", status_code=status.HTTP_201_CREATED, response_model=EvaluatorOut, summary="Add an evaluator"
)
async def create_evaluator(
    project_id: uuid.UUID,
    body: EvaluatorSpec,
    request: Request,
    access: EvaluatorWriter,
    db: DbSession,
    clock: ClockDep,
) -> EvaluatorOut:
    """Check and store an evaluator. `409 NAME_TAKEN`; `404` for a prompt version or credential
    the project or organization lacks; `422 BUDGET_REQUIRED`, `RULE_NEEDS_EXPECTED_OUTPUT`,
    `CHANNEL_KIND`, `JUDGE_TEMPLATE_VARIABLES` or `EVALUATOR_MODEL_UNPRICED`. A judge names either
    `prompt_version_id` or a built-in `judge_template` (relevance, faithfulness, toxicity,
    format_compliance), which becomes the project's prompt `judges/<name>`."""
    locked_project_id = await lock_project_of(access, db)
    try:
        evaluator = await service.create_evaluator(
            db, locked_project_id, actor_of(access, request), body, now=clock()
        )
    except EVALUATOR_ERRORS as error:
        raise evaluator_problem(error) from None
    created = await _out(db, evaluator, clock)
    await db.commit()
    _log("evaluator_created", access, evaluator.id, kind=evaluator.kind)
    return created


@router.get("/{evaluator_id}", response_model=EvaluatorOut, summary="Get an evaluator")
async def get_evaluator(
    project_id: uuid.UUID,
    evaluator_id: uuid.UUID,
    access: EvaluatorReader,
    db: DbSession,
    clock: ClockDep,
) -> EvaluatorOut:
    evaluator = await queries.get_evaluator(db, access.require_project().id, evaluator_id)
    if evaluator is None:
        raise not_found()
    return await _out(db, evaluator, clock)


@router.patch("/{evaluator_id}", response_model=EvaluatorOut, summary="Edit an evaluator")
async def update_evaluator(
    project_id: uuid.UUID,
    evaluator_id: uuid.UUID,
    body: EvaluatorPatch,
    request: Request,
    access: EvaluatorWriter,
    db: DbSession,
    clock: ClockDep,
) -> EvaluatorOut:
    """Change the members sent; `config` takes only the fields to change and keeps its `kind`.
    The result is checked as a create is."""
    locked_project_id = await lock_project_of(access, db)
    try:
        evaluator = await service.update_evaluator(
            db, locked_project_id, evaluator_id, actor_of(access, request), body, now=clock()
        )
    except EVALUATOR_ERRORS as error:
        raise evaluator_problem(error) from None
    updated = await _out(db, evaluator, clock)
    await db.commit()
    _log("evaluator_updated", access, evaluator_id, changed=sorted(body.model_fields_set))
    return updated


@router.delete(
    "/{evaluator_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete an evaluator"
)
async def delete_evaluator(
    project_id: uuid.UUID,
    evaluator_id: uuid.UUID,
    request: Request,
    access: EvaluatorWriter,
    db: DbSession,
) -> None:
    """Delete the evaluator and its runs. The scores it wrote stay."""
    locked_project_id = await lock_project_of(access, db)
    try:
        await service.delete_evaluator(
            db, locked_project_id, evaluator_id, actor_of(access, request)
        )
    except EVALUATOR_ERRORS as error:
        raise evaluator_problem(error) from None
    await db.commit()
    _log("evaluator_deleted", access, evaluator_id)
