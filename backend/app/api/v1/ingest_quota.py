"""Ingest quota: a project's effective ingest caps, today's usage and the spans its caps refused."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import ClockDep, DbSession, ReadAccess, SettingsDep, require
from app.api.schemas.ingest_quota import IngestQuotaOut
from app.api.window import Window
from app.core.permissions import Permission
from app.ingest.quota_report import ingest_quota_report

router = APIRouter(prefix="/projects/{project_id}", tags=["providers"])

# Also readable with an API key that has `traces:read`, for a sender checking its own quota.
ProjectReader = Annotated[ReadAccess, Depends(require(Permission.PROJECT_READ, allow_api_key=True))]


@router.get("/ingest-quota", response_model=IngestQuotaOut, summary="Ingest quota")
async def get_ingest_quota(
    project_id: uuid.UUID,
    access: ProjectReader,
    db: DbSession,
    settings: SettingsDep,
    window: Window,
    clock: ClockDep,
) -> IngestQuotaOut:
    """The project's effective ingest caps (`value` null when unlimited, `source` saying whether it
    is this project's own setting or the instance default), what it admitted today, and the spans
    its caps refused per UTC day over the window. Refused spans are counted once per refused
    request; a client that retried may have stored them after all. Readable with a session, a
    personal access token or a `traces:read` API key of the project.
    """
    project = access.require_project()
    return await ingest_quota_report(db, project, settings, window, clock())
