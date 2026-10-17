"""Exports: request a file of filtered traces or of the attribution table, list requests, and
get a download link.

The file is written by the `create_export` job to object storage; these routes only record the
request and report its progress. A personal access token or session may use them, an API key
may not.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.deps import Access, DbSession, SettingsDep, require
from app.api.schemas import Page
from app.core.errors import not_found
from app.core.idempotency import idempotent
from app.core.pagination import DEFAULT_PAGE_SIZE, PageLimit
from app.core.permissions import Permission
from app.core.security import mark_uncacheable
from app.exports.schemas import ExportCreateIn, ExportOut
from app.exports.service import (
    create_export,
    export_out,
    get_export,
    list_exports,
    require_object_store,
)
from app.storage.object_store import get_object_store

router = APIRouter(prefix="/projects/{project_id}", tags=["exports"])

ProjectReader = Annotated[Access, Depends(require(Permission.PROJECT_READ))]
# Authorization runs before the Idempotency-Key is reserved, so a retry is authorized like the
# request it repeats.
ExportCreator = Annotated[Access, Depends(idempotent(require(Permission.EXPORT_CREATE)))]


@router.post(
    "/exports",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ExportOut,
    summary="Request an export",
)
async def create_trace_export(
    project_id: uuid.UUID,
    body: ExportCreateIn,
    access: ExportCreator,
    db: DbSession,
    settings: SettingsDep,
) -> ExportOut:
    """Queue an export of the traces matching `filters` (`kind: traces`, the default) or of the
    attribution table of a window (`kind: attribution`, CSV only); `from` and `to` are required.

    An attribution export over more than 24 hours reads the daily rollups, so a metadata key must
    be promoted in the project settings (`422 KEY_NOT_PROMOTED` otherwise).

    Answers `409 NOT_CONFIGURED` when object storage is not set up. Poll `GET .../exports/{id}`
    until `status` is `done`, then download from `download_url`.
    """
    project = access.require_project()
    store = require_object_store(settings)
    export = await create_export(db, project=project, user_id=access.user_id, body=body)
    await db.commit()
    return export_out(export, store)


@router.get("/exports", response_model=Page[ExportOut], summary="List a project's exports")
async def list_trace_exports(
    project_id: uuid.UUID,
    access: ProjectReader,
    db: DbSession,
    settings: SettingsDep,
    response: Response,
    limit: PageLimit = DEFAULT_PAGE_SIZE,
    cursor: Annotated[str | None, Query()] = None,
) -> Page[ExportOut]:
    """Newest first. A finished export's `download_url` is a signed link, so nothing caches this."""
    mark_uncacheable(response)
    project = access.require_project()
    exports, next_cursor = await list_exports(db, project.id, limit=limit, cursor=cursor)
    store = get_object_store(settings)
    return Page[ExportOut](
        items=[export_out(export, store) for export in exports], next_cursor=next_cursor
    )


@router.get("/exports/{export_id}", response_model=ExportOut, summary="Get one export")
async def get_trace_export(
    project_id: uuid.UUID,
    export_id: uuid.UUID,
    access: ProjectReader,
    db: DbSession,
    settings: SettingsDep,
    response: Response,
) -> ExportOut:
    """The export's progress. `download_url` is a link valid for one hour, present once `done`.

    Anyone holding that link can download the file until it expires, so nothing caches this.
    """
    mark_uncacheable(response)
    project = access.require_project()
    export = await get_export(db, project.id, export_id)
    if export is None:
        raise not_found()
    return export_out(export, get_object_store(settings))
