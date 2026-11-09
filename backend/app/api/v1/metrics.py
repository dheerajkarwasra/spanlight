"""Project metrics: KPI overview, time series and per-model breakdown.

The routes only authorize and serialize. What the numbers mean, and which store they are read
from, is in ``app.metrics.service``.
"""

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request, Response

from app.api.deps import DbSession, ReadAccess, require
from app.api.schemas import ModelMetricsOut, OverviewOut, TimeseriesPointOut, TokenMixOut
from app.api.schemas.common import AGGREGATE_RESPONSE_HEADERS
from app.api.window import Window
from app.core.permissions import Permission
from app.live.cache import cached_answer
from app.metrics import service
from app.metrics.token_mix import token_mix
from app.rollups.coverage_reads import CoverageState

router = APIRouter(prefix="/projects/{project_id}/metrics", tags=["metrics"])

# Also readable by an API key with `traces:read`, for the project it belongs to.
ProjectReader = Annotated[ReadAccess, Depends(require(Permission.PROJECT_READ, allow_api_key=True))]
EnvironmentFilter = Annotated[str | None, Query(max_length=64)]


@router.get("/overview", response_model=OverviewOut)
async def overview(
    project_id: uuid.UUID,
    request: Request,
    access: ProjectReader,
    db: DbSession,
    window: Window,
    environment: EnvironmentFilter = None,
) -> OverviewOut:
    project = access.require_project()
    return await cached_answer(
        request, access, lambda _: service.overview(db, project.id, window, environment)
    )


@router.get(
    "/timeseries", response_model=list[TimeseriesPointOut], responses=AGGREGATE_RESPONSE_HEADERS
)
async def timeseries(
    project_id: uuid.UUID,
    request: Request,
    response: Response,
    access: ProjectReader,
    db: DbSession,
    window: Window,
    environment: EnvironmentFilter = None,
    bucket: Literal["hour", "day"] = "hour",
) -> list[TimeseriesPointOut]:
    project = access.require_project()

    async def compute(headers: dict[str, str]) -> list[TimeseriesPointOut]:
        coverage = CoverageState()
        result = await service.timeseries(db, project.id, window, environment, bucket, coverage)
        _set_aggregate_headers(headers, coverage)
        return result

    return await cached_answer(request, access, compute, response)


@router.get("/models", response_model=list[ModelMetricsOut], responses=AGGREGATE_RESPONSE_HEADERS)
async def models(
    project_id: uuid.UUID,
    request: Request,
    response: Response,
    access: ProjectReader,
    db: DbSession,
    window: Window,
    environment: EnvironmentFilter = None,
) -> list[ModelMetricsOut]:
    project = access.require_project()

    async def compute(headers: dict[str, str]) -> list[ModelMetricsOut]:
        coverage = CoverageState()
        result = await service.models(db, project.id, window, environment, coverage)
        _set_aggregate_headers(headers, coverage)
        return result

    return await cached_answer(request, access, compute, response)


def _set_aggregate_headers(headers: dict[str, str], coverage: CoverageState) -> None:
    headers["Aggregate-Status"] = coverage.status
    if coverage.oldest_pending_at is not None:
        headers["Aggregate-Oldest-Pending-At"] = coverage.oldest_pending_at.isoformat()


@router.get("/token-mix", response_model=TokenMixOut)
async def get_token_mix(
    project_id: uuid.UUID, access: ProjectReader, db: DbSession, window: Window
) -> TokenMixOut:
    """Tokens by price class over the UTC days the window touches, all environments."""
    project = access.require_project()
    return await token_mix(db, project.id, window)
