"""Attribution: a project's spend and usage grouped by metadata, trace name, user or release.

The route only authorizes and serializes. Which source answers and what the figures mean is in
``app.attribution.service``.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import DbSession, ReadAccess, require
from app.api.params import NO_NUL
from app.api.schemas import AttributionOut
from app.api.window import Window
from app.attribution import service
from app.attribution.aggregate_sql import GroupKind
from app.attribution.queries import AttributionSort
from app.attribution.service import AttributionRequest
from app.core.observability import feature_used
from app.core.pagination import DEFAULT_PAGE_SIZE, PageLimit
from app.core.permissions import Permission
from app.ingest.metadata import MetadataKey

router = APIRouter(prefix="/projects/{project_id}/attribution", tags=["attribution"])

# Also readable by an API key with `traces:read`, for the project it belongs to.
ProjectReader = Annotated[ReadAccess, Depends(require(Permission.PROJECT_READ, allow_api_key=True))]
Environment = Annotated[str | None, Query(max_length=64, pattern=NO_NUL)]


@router.get("", response_model=AttributionOut)
async def read_attribution(
    project_id: uuid.UUID,
    access: ProjectReader,
    db: DbSession,
    window: Window,
    group_by: GroupKind,
    key: Annotated[MetadataKey | None, Query()] = None,
    environment: Environment = None,
    sort: AttributionSort = "cost",
    limit: PageLimit = DEFAULT_PAGE_SIZE,
    cursor: Annotated[str | None, Query()] = None,
) -> AttributionOut:
    project = access.require_project()
    request = AttributionRequest(group_by, key, window, environment, sort, limit, cursor)
    out = await service.read_attribution(db, project.id, project.promoted_metadata_keys, request)
    feature_used("attribution_view")
    return out
