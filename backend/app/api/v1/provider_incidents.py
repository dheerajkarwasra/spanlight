"""The providers' status page incidents and the state of the feeds they are polled from.

Incidents are global (no tenant data); the route is still project-scoped, like the page that
shows it, and needs `project:read`. No API key.
"""

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query

from app.api.deps import Access, DbSession, SettingsDep, require
from app.api.schemas.providers import IncidentListOut, IncidentOut, StatusSourceOut
from app.api.window import Window
from app.core.observability import feature_used
from app.core.pagination import decode_uuid_cursor, encode_cursor
from app.core.permissions import Permission
from app.provider_status import service

router = APIRouter(prefix="/projects/{project_id}", tags=["providers"])

ProjectReader = Annotated[Access, Depends(require(Permission.PROJECT_READ))]
StatusProvider = Literal["anthropic", "openai", "google"]


@router.get("/provider-incidents", response_model=IncidentListOut, summary="Provider incidents")
async def list_provider_incidents(
    project_id: uuid.UUID,
    access: ProjectReader,
    db: DbSession,
    settings: SettingsDep,
    window: Window,
    provider: StatusProvider | None = None,
    api_only: bool = True,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> IncidentListOut:
    """Incidents that overlap the window, newest first by `(started_at, id)`.

    An incident overlaps `[from, to)` when it started before `to` and had not been resolved by
    `from` (an open one has not ended). `api_only` (default true) keeps the incidents that touch
    the providers' APIs. `sources` is every feed's poll state, and `polling` says whether this
    server polls the feeds at all (`PROVIDER_STATUS_POLLING_ENABLED`).
    """
    access.require_project()
    after = decode_uuid_cursor(cursor) if cursor else None
    rows = await service.list_incidents(
        db, window=window, provider=provider, api_only=api_only, limit=limit + 1, after=after
    )
    sources = await service.list_sources(db)
    feature_used("provider_incidents")
    page = rows[:limit]
    last = page[-1] if len(rows) > limit else None
    return IncidentListOut(
        polling="enabled" if settings.provider_status_polling_enabled else "disabled",
        sources=[StatusSourceOut.model_validate(source) for source in sources],
        items=[IncidentOut.model_validate(row) for row in page],
        next_cursor=encode_cursor(last.started_at, str(last.id)) if last else None,
    )
