"""Scores on the dashboard: a project's score configs, a trace's scores, and human scoring.

Members read configs and scores (`project:read`) and add scores and configs (`scores:write`).
A person may delete the scores they added; evaluator and API scores, and other people's, need an
admin. Ingestion of scores by API key (`/v1/scores`) lives with the other ingestion routes.
Responses are built before the commit, which ends the project binding that row-level security
needs.
"""

import uuid
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Request, status

from app.annotations.service import queue_uses_config
from app.api.deps import Access, ClockDep, DbSession, client_ip, require
from app.api.locks import lock_project_of
from app.api.schemas import ScoreConfigOut, ScoreConfigPatch, ScoreConfigSpec, ScoreCreate, ScoreOut
from app.api.schemas.scores import FailureCategoriesIn
from app.api.score_problems import CONFIG_ERRORS, config_problem, score_rejection
from app.core.errors import conflict, forbidden, not_found
from app.core.ids import new_id
from app.core.observability import feature_used
from app.core.permissions import Permission
from app.scores import configs, queries
from app.scores.errors import ScoreDeleteForbiddenError, ScoreNotFoundError
from app.scores.failure_category import create_failure_category_config
from app.scores.schemas import ScoreIn
from app.scores.service import ScoreActor, delete_score, write_scores
from app.scores.types import ScoreSource

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/projects/{project_id}", tags=["scores"])

ScoreReader = Annotated[Access, Depends(require(Permission.PROJECT_READ))]
ScoreWriter = Annotated[Access, Depends(require(Permission.SCORES_WRITE))]


def _log(event: str, access: Access, **fields: object) -> None:
    logger.info(
        event, org_id=str(access.org.id), project_id=str(access.require_project().id), **fields
    )


@router.get("/score-configs", response_model=list[ScoreConfigOut], summary="List score configs")
async def list_score_configs(
    project_id: uuid.UUID, access: ScoreReader, db: DbSession
) -> list[ScoreConfigOut]:
    """By name. Unpaginated: a project holds at most 500 score configs."""
    rows = await queries.list_configs(db, access.require_project().id)
    return [ScoreConfigOut.model_validate(config) for config in rows]


@router.post(
    "/score-configs",
    status_code=status.HTTP_201_CREATED,
    response_model=ScoreConfigOut,
    summary="Add a score config",
)
async def create_score_config(
    project_id: uuid.UUID,
    body: ScoreConfigSpec,
    request: Request,
    access: ScoreWriter,
    db: DbSession,
    clock: ClockDep,
) -> ScoreConfigOut:
    """Define a score name. `409 NAME_TAKEN` when the name has a config, including one the
    first score created: edit that one instead."""
    locked_project_id = await lock_project_of(access, db)
    try:
        config = await configs.create_config(
            db,
            access.org.id,
            locked_project_id,
            access.user_id,
            body,
            now=clock(),
            ip=client_ip(request),
        )
    except CONFIG_ERRORS as error:
        raise config_problem(error) from None
    created = ScoreConfigOut.model_validate(config)
    await db.commit()
    _log("score_config_created", access, config_id=str(config.id))
    return created


@router.post(
    "/score-configs/failure-category",
    status_code=status.HTTP_201_CREATED,
    response_model=ScoreConfigOut,
    summary="Add the failure-category config",
)
async def create_failure_category(
    project_id: uuid.UUID,
    body: FailureCategoriesIn,
    request: Request,
    access: ScoreWriter,
    db: DbSession,
    clock: ClockDep,
) -> ScoreConfigOut:
    """Create the categorical `failure_category` config with the categories sent, in that
    order (2 to 30, distinct, `none` included). `409 NAME_TAKEN` when the project has one:
    edit its list with `PATCH /score-configs/{config_id}`."""
    locked_project_id = await lock_project_of(access, db)
    try:
        config = await create_failure_category_config(
            db,
            access.org.id,
            locked_project_id,
            access.user_id,
            body,
            now=clock(),
            ip=client_ip(request),
        )
    except CONFIG_ERRORS as error:
        raise config_problem(error) from None
    created = ScoreConfigOut.model_validate(config)
    await db.commit()
    feature_used("failure_category")
    _log("failure_category_config_created", access, config_id=str(config.id))
    return created


@router.patch(
    "/score-configs/{config_id}", response_model=ScoreConfigOut, summary="Edit a score config"
)
async def update_score_config(
    project_id: uuid.UUID,
    config_id: uuid.UUID,
    body: ScoreConfigPatch,
    request: Request,
    access: ScoreWriter,
    db: DbSession,
    clock: ClockDep,
) -> ScoreConfigOut:
    """Change the description, range or categories; the config becomes explicit. A new
    `data_type` is `409 SCORE_CONFIG_IN_USE` once scores use the name. New bounds apply to new
    scores only."""
    locked_project_id = await lock_project_of(access, db)
    try:
        config = await configs.update_config(
            db,
            access.org.id,
            locked_project_id,
            config_id,
            access.user_id,
            body,
            now=clock(),
            ip=client_ip(request),
        )
    except CONFIG_ERRORS as error:
        raise config_problem(error) from None
    updated = ScoreConfigOut.model_validate(config)
    await db.commit()
    _log("score_config_updated", access, config_id=str(config_id))
    return updated


@router.delete(
    "/score-configs/{config_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a score config",
)
async def delete_score_config(
    project_id: uuid.UUID,
    config_id: uuid.UUID,
    request: Request,
    access: ScoreWriter,
    db: DbSession,
) -> None:
    """`409 SCORE_CONFIG_IN_USE` while scores carry the name or a review queue asks for it."""
    locked_project_id = await lock_project_of(access, db)
    # Queues are created under the same project lock, so none can start using it meanwhile.
    if await queue_uses_config(db, locked_project_id, config_id):
        raise conflict(
            "SCORE_CONFIG_IN_USE",
            "A review queue asks for this score, so the config cannot be deleted.",
        )
    try:
        await configs.delete_config(
            db, access.org.id, locked_project_id, config_id, access.user_id, ip=client_ip(request)
        )
    except CONFIG_ERRORS as error:
        raise config_problem(error) from None
    await db.commit()
    _log("score_config_deleted", access, config_id=str(config_id))


@router.get(
    "/traces/{trace_id}/scores", response_model=list[ScoreOut], summary="List a trace's scores"
)
async def list_trace_scores(
    project_id: uuid.UUID, trace_id: str, access: ScoreReader, db: DbSession
) -> list[ScoreOut]:
    """Every score of the trace, latest first (at most 1000). A score may name a trace that has
    not arrived yet, so an unknown trace lists its scores too."""
    rows = await queries.trace_scores(db, access.require_project().id, trace_id.lower())
    return [ScoreOut.of(score, author) for score, author in rows]


@router.post(
    "/traces/{trace_id}/scores",
    status_code=status.HTTP_201_CREATED,
    response_model=ScoreOut,
    summary="Score a trace",
)
async def create_trace_score(
    project_id: uuid.UUID,
    trace_id: str,
    body: ScoreCreate,
    access: ScoreWriter,
    db: DbSession,
) -> ScoreOut:
    """Add a `human` score by the caller. `404` when the trace is not in the project; `422
    VALIDATION_ERROR` when the score does not fit its name's config. A first score with a new
    name creates its config.

    `id` makes a retry idempotent: when a score with that id already exists in the project,
    nothing is written and the stored score is returned as it is, even if it belongs to another
    trace or author."""
    project = access.require_project()
    trace_id = trace_id.lower()
    if not await queries.trace_exists(db, project.id, trace_id):
        raise not_found()
    score_id = body.id or new_id()
    score = ScoreIn.model_validate(
        {**body.model_dump(exclude_unset=True), "id": score_id, "trace_id": trace_id}
    )
    outcome = await write_scores(
        db, project.id, [score], source=ScoreSource.HUMAN, author_user_id=access.user_id
    )
    if outcome.rejected:
        raise score_rejection(outcome.rejected[0].reason)
    stored = await queries.get_score(db, project.id, score_id)
    if stored is None:
        raise not_found()
    created = ScoreOut.of(*stored)
    await db.commit()
    _log("score_created", access, score_id=str(score_id))
    return created


@router.delete(
    "/scores/{score_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a score"
)
async def delete_trace_score(
    project_id: uuid.UUID, score_id: uuid.UUID, access: ScoreWriter, db: DbSession
) -> None:
    """Your own human score, or any score as an admin; `403 FORBIDDEN` otherwise."""
    actor = ScoreActor(user_id=access.user_id, is_admin=access.can(Permission.PROJECT_WRITE))
    try:
        await delete_score(db, access.require_project().id, score_id, actor)
    except ScoreNotFoundError:
        raise not_found() from None
    except ScoreDeleteForbiddenError:
        raise forbidden("Only the score's author or an admin can delete it.") from None
    await db.commit()
    _log("score_deleted", access, score_id=str(score_id))
