"""Project metrics: KPI overview, time series and per-model breakdown.

The routes only authorize and serialize. What the numbers mean, and which store they are read
from, is in ``app.metrics.service``.
"""

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query

from app.api.deps import DbSession, ReadAccess, require
from app.api.schemas import ModelMetricsOut, OverviewOut, TimeseriesPointOut, TokenMixOut
from app.api.window import Window
from app.core.permissions import Permission
from app.metrics import service
from app.metrics.token_mix import token_mix

router = APIRouter(prefix="/projects/{project_id}/metrics", tags=["metrics"])

# Also readable by an API key with `traces:read`, for the project it belongs to.
ProjectReader = Annotated[ReadAccess, Depends(require(Permission.PROJECT_READ, allow_api_key=True))]
EnvironmentFilter = Annotated[str | None, Query(max_length=64)]


@router.get("/overview", response_model=OverviewOut)
async def overview(
    project_id: uuid.UUID,
    access: ProjectReader,
    db: DbSession,
    window: Window,
    environment: EnvironmentFilter = None,
) -> OverviewOut:
    project = access.require_project()
    return await service.overview(db, project.id, window, environment)


@router.get("/timeseries", response_model=list[TimeseriesPointOut])
async def timeseries(
    project_id: uuid.UUID,
    access: ProjectReader,
    db: DbSession,
    window: Window,
    environment: EnvironmentFilter = None,
    bucket: Literal["hour", "day"] = "hour",
) -> list[TimeseriesPointOut]:
    project = access.require_project()
    return await service.timeseries(db, project.id, window, environment, bucket)


@router.get("/models", response_model=list[ModelMetricsOut])
async def models(
    project_id: uuid.UUID,
    access: ProjectReader,
    db: DbSession,
    window: Window,
    environment: EnvironmentFilter = None,
) -> list[ModelMetricsOut]:
    project = access.require_project()
    return await service.models(db, project.id, window, environment)


@router.get("/token-mix", response_model=TokenMixOut)
async def get_token_mix(
    project_id: uuid.UUID, access: ProjectReader, db: DbSession, window: Window
) -> TokenMixOut:
    """Tokens by price class over the UTC days the window touches, all environments."""
    project = access.require_project()
    return await token_mix(db, project.id, window)
