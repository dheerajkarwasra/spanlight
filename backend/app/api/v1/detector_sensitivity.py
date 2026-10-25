"""Doctor sensitivity: each check's setting, a preview of every setting, and changing one.

Anyone who can read the project reads the settings and previews (`project:read`; an API key does
not); owners and admins change them (`insights:manage`). A change applies from the next detector
run. Responses are built before the commit, which ends the project binding that row-level
security needs.
"""

import uuid
from dataclasses import asdict
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Path, Request

from app.api.deps import Access, ClockDep, DbSession, client_ip, require
from app.api.locks import lock_project_of
from app.api.schemas.detector_sensitivity import (
    SensitivityIn,
    SensitivityListOut,
    SensitivityOut,
    SensitivityPreviewOut,
    WindowCountsOut,
)
from app.core.permissions import Permission
from app.insights import sensitivity_service as service
from app.insights.service import Actor

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/projects/{project_id}/detector-sensitivity", tags=["insights"])

SensitivityReader = Annotated[Access, Depends(require(Permission.PROJECT_READ))]
SensitivityManager = Annotated[Access, Depends(require(Permission.INSIGHTS_MANAGE))]
Detector = Annotated[str, Path(min_length=1, max_length=64)]


@router.get("", response_model=SensitivityListOut, summary="List Doctor sensitivity settings")
async def list_sensitivity(
    project_id: uuid.UUID, access: SensitivityReader, db: DbSession, clock: ClockDep
) -> SensitivityListOut:
    """All fifteen checks in catalogue order; a fixed check has `tunable: false` and no gates."""
    items = await service.list_items(db, access.require_project().id, clock())
    return SensitivityListOut(items=[SensitivityOut(**asdict(item)) for item in items])


@router.get(
    "/{detector}/preview",
    response_model=SensitivityPreviewOut,
    summary="Preview each sensitivity setting",
)
async def preview_sensitivity(
    project_id: uuid.UUID,
    detector: Detector,
    access: SensitivityReader,
    db: DbSession,
    clock: ClockDep,
) -> SensitivityPreviewOut:
    """Findings each setting would have shown over 7 and 30 days, from stored findings.
    `404 DETECTOR_NOT_TUNABLE` for a fixed or unknown check."""
    windows = await service.preview_counts(db, access.require_project().id, detector, clock())
    return SensitivityPreviewOut(
        detector=detector, windows=[WindowCountsOut(**asdict(window)) for window in windows]
    )


@router.put("/{detector}", response_model=SensitivityOut, summary="Set a check's sensitivity")
async def set_sensitivity(
    project_id: uuid.UUID,
    detector: Detector,
    body: SensitivityIn,
    request: Request,
    access: SensitivityManager,
    db: DbSession,
    clock: ClockDep,
) -> SensitivityOut:
    """Applies from the next detector run; open findings below the new setting resolve through
    the 24-hour quiet rule. Setting the current level changes nothing.
    `404 DETECTOR_NOT_TUNABLE` for a fixed or unknown check."""
    locked_project_id = await lock_project_of(access, db)
    actor = Actor(org_id=access.org.id, user_id=access.user_id, ip=client_ip(request))
    item = await service.set_level(db, locked_project_id, detector, body.level, actor, clock())
    out = SensitivityOut(**asdict(item))
    await db.commit()
    logger.info(
        "detector_sensitivity_set",
        org_id=str(access.org.id),
        project_id=str(locked_project_id),
        detector=detector,
        level=body.level,
    )
    return out
