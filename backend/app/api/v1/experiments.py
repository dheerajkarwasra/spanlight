"""Experiments: replay a dataset through a prompt or messages and score every answer.

Members list and read experiments, runs and items (`project:read`); members create experiments
and start runs (`experiments:run`), which spend money in the project. A run is queued here and
executed by the worker's `run_experiment` job. Responses are built before the commit, which ends
the project binding that row-level security needs.
"""

import uuid
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Query, Request, status

from app.api.deps import Access, ClockDep, DbSession, SettingsDep, require
from app.api.evaluator_problems import actor_of
from app.api.experiment_problems import EXPERIMENT_ERRORS, experiment_problem
from app.api.locks import lock_project_of
from app.api.schemas.common import Page
from app.api.schemas.experiments import (
    ExperimentItemOut,
    ExperimentOut,
    ExperimentRunOut,
    ExperimentSpec,
    RunStart,
)
from app.core.errors import not_found
from app.core.idempotency import idempotent
from app.core.observability import feature_used
from app.core.pagination import PageLimit, decode_uuid_cursor, encode_cursor
from app.core.permissions import Permission
from app.evals import experiment_queries as queries
from app.evals import experiments as service

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/projects/{project_id}/experiments", tags=["experiments"])

ExperimentReader = Annotated[Access, Depends(require(Permission.PROJECT_READ))]
ExperimentWriter = Annotated[Access, Depends(require(Permission.EXPERIMENTS_RUN))]
ExperimentRunner = Annotated[Access, Depends(idempotent(require(Permission.EXPERIMENTS_RUN)))]
Cursor = Annotated[str | None, Query(max_length=512)]

DEFAULT_PAGE = 50


def _log(event: str, access: Access, experiment_id: uuid.UUID, **fields: object) -> None:
    logger.info(
        event,
        org_id=str(access.org.id),
        project_id=str(access.require_project().id),
        experiment_id=str(experiment_id),
        **fields,
    )


@router.get("", response_model=Page[ExperimentOut], summary="List experiments")
async def list_experiments(
    project_id: uuid.UUID,
    access: ExperimentReader,
    db: DbSession,
    limit: PageLimit = DEFAULT_PAGE,
    cursor: Cursor = None,
) -> Page[ExperimentOut]:
    """Newest first, each with its run count and latest run (one query for the page)."""
    after = decode_uuid_cursor(cursor) if cursor else None
    rows = await queries.list_experiments(db, access.require_project().id, after=after, limit=limit)
    shown, more = rows[:limit], len(rows) > limit
    summaries = await queries.run_summaries(db, [experiment.id for experiment in shown])
    last = shown[-1] if shown else None
    return Page(
        items=[ExperimentOut.of(experiment, summaries[experiment.id]) for experiment in shown],
        next_cursor=encode_cursor(last.created_at, str(last.id)) if more and last else None,
    )


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=ExperimentOut,
    summary="Add an experiment",
)
async def create_experiment(
    project_id: uuid.UUID,
    body: ExperimentSpec,
    request: Request,
    access: ExperimentWriter,
    db: DbSession,
    clock: ClockDep,
    settings: SettingsDep,
) -> ExperimentOut:
    """Check and store an experiment. `409 NAME_TAKEN`; `404` for a dataset, evaluator or prompt
    version the project lacks or a credential the organization lacks; `422
    EXPERIMENT_MODEL_UNPRICED`, or `VALIDATION_ERROR` for `max_items` over the server's limit."""
    locked_project_id = await lock_project_of(access, db)
    try:
        experiment = await service.create_experiment(
            db,
            locked_project_id,
            actor_of(access, request),
            body,
            max_items_ceiling=settings.experiment_max_items,
            now=clock(),
        )
    except EXPERIMENT_ERRORS as error:
        raise experiment_problem(error) from None
    created = ExperimentOut.of(experiment)
    await db.commit()
    _log("experiment_created", access, experiment.id)
    return created


@router.get("/{experiment_id}", response_model=ExperimentOut, summary="Get an experiment")
async def get_experiment(
    project_id: uuid.UUID, experiment_id: uuid.UUID, access: ExperimentReader, db: DbSession
) -> ExperimentOut:
    experiment = await queries.get_experiment(db, access.require_project().id, experiment_id)
    if experiment is None:
        raise not_found()
    summaries = await queries.run_summaries(db, [experiment.id])
    return ExperimentOut.of(experiment, summaries[experiment.id])


@router.post(
    "/{experiment_id}/runs",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ExperimentRunOut,
    summary="Run an experiment",
)
async def start_run(
    project_id: uuid.UUID,
    experiment_id: uuid.UUID,
    request: Request,
    access: ExperimentRunner,
    db: DbSession,
    clock: ClockDep,
    body: RunStart | None = None,
) -> ExperimentRunOut:
    """Queue a run; the worker executes it. Send an `Idempotency-Key` to make a retry safe (the
    body is part of what a retry must repeat). `target_override` runs a prompt-version experiment
    with another version of the same prompt, same credential and model (`422
    TARGET_OVERRIDE_INVALID` otherwise). `409 RUN_IN_PROGRESS` while another run of the
    experiment is queued or running."""
    locked_project_id = await lock_project_of(access, db)
    override = body.target_override if body is not None else None
    try:
        run = await service.start_run(
            db,
            locked_project_id,
            actor_of(access, request),
            experiment_id,
            override,
            now=clock(),
        )
    except EXPERIMENT_ERRORS as error:
        raise experiment_problem(error) from None
    queued = ExperimentRunOut.of(run)
    await db.commit()
    _log("experiment_run_queued", access, experiment_id, run_id=str(run.id))
    return queued


@router.get("/{experiment_id}/runs", response_model=Page[ExperimentRunOut], summary="List runs")
async def list_runs(
    project_id: uuid.UUID,
    experiment_id: uuid.UUID,
    access: ExperimentReader,
    db: DbSession,
    limit: PageLimit = DEFAULT_PAGE,
    cursor: Cursor = None,
) -> Page[ExperimentRunOut]:
    """Newest first, with the totals stored when each run ended (null while unfinished; read
    the run for live totals)."""
    experiment = await queries.get_experiment(db, access.require_project().id, experiment_id)
    if experiment is None:
        raise not_found()
    after = decode_uuid_cursor(cursor) if cursor else None
    rows = await queries.list_runs(db, experiment.id, after=after, limit=limit)
    shown, more = rows[:limit], len(rows) > limit
    last = shown[-1] if shown else None
    return Page(
        items=[ExperimentRunOut.of(run) for run in shown],
        next_cursor=encode_cursor(last.started_at, str(last.id)) if more and last else None,
    )


@router.get("/{experiment_id}/runs/{run_id}", response_model=ExperimentRunOut, summary="Get a run")
async def get_run(
    project_id: uuid.UUID,
    experiment_id: uuid.UUID,
    run_id: uuid.UUID,
    access: ExperimentReader,
    db: DbSession,
) -> ExperimentRunOut:
    """The run with its totals: stored when it ended, computed from its items while it runs."""
    try:
        _, run = await service.get_run(db, access.require_project().id, experiment_id, run_id)
    except EXPERIMENT_ERRORS as error:
        raise experiment_problem(error) from None
    totals = await service.run_totals(db, access.require_project().id, run)
    if any(stat.ci_low is not None for stat in totals.scores.values()):
        feature_used("experiment_interval")
    return ExperimentRunOut.of(run, totals)


@router.get(
    "/{experiment_id}/runs/{run_id}/items",
    response_model=Page[ExperimentItemOut],
    summary="List a run's items",
)
async def list_run_items(
    project_id: uuid.UUID,
    experiment_id: uuid.UUID,
    run_id: uuid.UUID,
    access: ExperimentReader,
    db: DbSession,
    limit: PageLimit = DEFAULT_PAGE,
    cursor: Cursor = None,
) -> Page[ExperimentItemOut]:
    """In dataset item order."""
    try:
        _, run = await service.get_run(db, access.require_project().id, experiment_id, run_id)
    except EXPERIMENT_ERRORS as error:
        raise experiment_problem(error) from None
    after = decode_uuid_cursor(cursor)[1] if cursor else None
    rows = await queries.list_items(db, run.id, after=after, limit=limit)
    shown, more = rows[:limit], len(rows) > limit
    last = shown[-1] if shown else None
    return Page(
        items=[ExperimentItemOut.of(item) for item in shown],
        next_cursor=(
            encode_cursor(last.created_at, str(last.dataset_item_id)) if more and last else None
        ),
    )
