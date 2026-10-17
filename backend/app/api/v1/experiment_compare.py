"""Compare two runs of an experiment (`project:read`)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import Access, DbSession, SettingsDep, require
from app.api.experiment_problems import EXPERIMENT_ERRORS, experiment_problem
from app.api.schemas.experiment_compare import ComparisonOut
from app.core.observability import feature_used
from app.core.permissions import Permission
from app.evals.experiment_compare import compare_experiment_runs

router = APIRouter(prefix="/projects/{project_id}/experiments", tags=["experiments"])

ExperimentReader = Annotated[Access, Depends(require(Permission.PROJECT_READ))]


@router.get(
    "/{experiment_id}/runs/{run_a}/compare/{run_b}",
    response_model=ComparisonOut,
    summary="Compare two runs",
)
async def compare_runs(
    project_id: uuid.UUID,
    experiment_id: uuid.UUID,
    run_a: uuid.UUID,
    run_b: uuid.UUID,
    access: ExperimentReader,
    db: DbSession,
    settings: SettingsDep,
) -> ComparisonOut:
    """Deltas are `b - a`. `404` when either run is not a run of this experiment; `409
    RUN_IN_PROGRESS` while either is queued or running."""
    try:
        comparison = await compare_experiment_runs(
            db,
            access.require_project().id,
            experiment_id,
            run_a,
            run_b,
            limit=settings.experiment_max_items,
        )
    except EXPERIMENT_ERRORS as error:
        raise experiment_problem(error) from None
    if any(s.ci_a or s.ci_b or s.test for s in comparison.aggregate.values()):
        feature_used("experiment_interval")
    return ComparisonOut.of(comparison)
