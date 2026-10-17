"""Release comparison: the releases seen in a window and how two of them differ.

The routes only authorize and serialize. What the figures mean is in ``app.releases.service``.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import DbSession, ReadAccess, require
from app.api.params import NO_NUL
from app.api.schemas import Comparison, FailureCategoriesOut, ReleaseStats
from app.api.window import Window
from app.core.permissions import Permission
from app.releases import service

router = APIRouter(prefix="/projects/{project_id}/releases", tags=["releases"])

# Also readable by an API key with `traces:read`, for the project it belongs to.
ProjectReader = Annotated[ReadAccess, Depends(require(Permission.PROJECT_READ, allow_api_key=True))]
EnvironmentFilter = Annotated[str | None, Query(max_length=64, pattern=NO_NUL)]
# The longest release a trace can carry (`TraceIn.release`).
ReleaseName = Annotated[str, Query(min_length=1, max_length=128, pattern=NO_NUL)]


@router.get("", response_model=list[ReleaseStats])
async def list_releases(
    project_id: uuid.UUID,
    access: ProjectReader,
    db: DbSession,
    window: Window,
    environment: EnvironmentFilter = None,
) -> list[ReleaseStats]:
    project = access.require_project()
    return await service.list_releases(db, project.id, window, environment)


# Declared before any `/{release}` path a later route may add, so the literal segment wins.
@router.get("/failure-categories", response_model=FailureCategoriesOut)
async def failure_category_rates(
    project_id: uuid.UUID,
    access: ProjectReader,
    db: DbSession,
    window: Window,
    environment: EnvironmentFilter = None,
) -> FailureCategoriesOut:
    """The rate of each failure category per release, with Wilson 95 % bounds, over the traces a
    person reviewed. `404 NOT_FOUND` until the project has a `failure_category` score config."""
    project = access.require_project()
    return await service.failure_category_rates(db, project.id, window, environment)


@router.get("/compare", response_model=Comparison)
async def compare_releases(
    project_id: uuid.UUID,
    access: ProjectReader,
    db: DbSession,
    window: Window,
    a: ReleaseName,
    b: ReleaseName,
    environment: EnvironmentFilter = None,
) -> Comparison:
    project = access.require_project()
    return await service.compare_releases(db, project.id, a, b, window, environment)
