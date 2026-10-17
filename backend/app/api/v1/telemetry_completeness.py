"""Telemetry completeness: the share of a project's spans that were stored rather than lost."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import Access, ClockDep, DbSession, require
from app.api.schemas.completeness import CompletenessOut
from app.api.window import Window
from app.core.observability import feature_used
from app.core.permissions import Permission
from app.telemetry_losses.report import completeness_report

router = APIRouter(prefix="/projects/{project_id}", tags=["providers"])

ProjectReader = Annotated[Access, Depends(require(Permission.PROJECT_READ))]


@router.get(
    "/telemetry-completeness", response_model=CompletenessOut, summary="Telemetry completeness"
)
async def get_telemetry_completeness(
    project_id: uuid.UUID, access: ProjectReader, db: DbSession, window: Window, clock: ClockDep
) -> CompletenessOut:
    """Spans stored in the window against the spans counted as lost: by the gateway, rejected at
    ingest, and dropped or failed in the SDK.

    `completeness` is `stored / (stored + every loss)` as a decimal string to four places,
    rounded down; null when nothing was stored and nothing lost. Losses are counted per hour, so
    an hour that only partly overlaps the window counts whole. `lower_bound` is true when both
    SDK `failed` and ingest rejections were counted, since a span the server rejected may be in
    both. Counts above 2^53 - 1 are sent as 2^53 - 1.
    """
    project = access.require_project()
    answer = await completeness_report(db, project.id, window, clock())
    feature_used("telemetry_completeness")
    return answer
