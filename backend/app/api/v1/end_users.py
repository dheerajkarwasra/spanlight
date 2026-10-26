"""End-user analytics: the users of a project and the detail of one.

The routes only authorize and serialize. What the figures mean is in ``app.end_users.service``.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request, status

from app.api.deps import Access, DbSession, ReadAccess, SettingsDep, client_ip, require
from app.api.params import NO_NUL, Environment
from app.api.schemas import EndUserErasureIn, EndUserErasureOut, UserDetailOut, UserListOut
from app.api.window import Window
from app.core.errors import forbidden, not_found
from app.core.idempotency import idempotent
from app.core.pagination import DEFAULT_PAGE_SIZE, PageLimit
from app.core.permissions import Permission
from app.end_users import service
from app.end_users.erasure_job import request_erasure
from app.end_users.queries import UserSort
from app.explorer.trace_sort import SortOrder
from app.services.deletion import lock_project_for_write

router = APIRouter(prefix="/projects/{project_id}/users", tags=["users"])

# Also readable by an API key with `traces:read`, for the project it belongs to.
ProjectReader = Annotated[ReadAccess, Depends(require(Permission.PROJECT_READ, allow_api_key=True))]
# Erasing a user's data is as destructive as deleting the project, so the same permission (admins
# and owners; a write permission, so a read-only token cannot). Authorization runs before the
# Idempotency-Key is reserved, so a retry is authorized like the request it repeats.
UserEraser = Annotated[Access, Depends(idempotent(require(Permission.PROJECT_DELETE)))]
# `:path` lets an id that contains a slash through. The longest id is `TraceIn.user_id`'s (256)
# plus the one-character `~` escape the service strips (see `service.unescape_user_id`).
UserId = Annotated[str, Path(min_length=1, max_length=257, pattern=NO_NUL)]


@router.get("", response_model=UserListOut)
async def list_users(
    project_id: uuid.UUID,
    access: ProjectReader,
    db: DbSession,
    window: Window,
    sort: UserSort = "cost",
    limit: PageLimit = DEFAULT_PAGE_SIZE,
    cursor: Annotated[str | None, Query()] = None,
    environment: Environment = None,
    q: Annotated[
        str | None,
        Query(min_length=1, max_length=256, pattern=NO_NUL, description="User id contains"),
    ] = None,
    order: SortOrder = "desc",
) -> UserListOut:
    project = access.require_project()
    return await service.list_users(
        db, project.id, window, sort, limit, cursor, environment, q, order
    )


@router.get("/{external_user_id:path}", response_model=UserDetailOut)
async def user_detail(
    project_id: uuid.UUID,
    external_user_id: UserId,
    access: ProjectReader,
    db: DbSession,
    window: Window,
    environment: Environment = None,
) -> UserDetailOut:
    project = access.require_project()
    return await service.user_detail(db, project.id, external_user_id, window, environment)


@router.post(
    "/erasures",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=EndUserErasureOut,
    summary="Erase one end user's data",
)
async def request_user_erasure(
    project_id: uuid.UUID,
    body: EndUserErasureIn,
    request: Request,
    access: UserEraser,
    db: DbSession,
    settings: SettingsDep,
) -> EndUserErasureOut:
    """Queue the erasure of everything stored for one end user of the project: traces and
    spans, scores, review-queue items, the dataset items copied from the user's traces and the
    experiment items and replays made from them, the user's daily statistics, attribution rows,
    budget alerts and export filters. `202` means queued: a worker job deletes in batches, and
    the organization's audit log gets an `end_user.erase` event with the counts when it is done.

    The id goes in the body because ids can contain `/`. The demo project refuses.
    """
    if access.org.is_demo:
        raise forbidden("The demo organization cannot be changed.")
    project = access.require_project()
    if not await lock_project_for_write(db, access.org.id, project.id):
        raise not_found()
    requested_at = await request_erasure(
        db,
        settings,
        org_id=access.org.id,
        project_id=project.id,
        external_user_id=body.external_user_id,
        actor_user_id=access.user_id,
        ip=client_ip(request),
    )
    await db.commit()
    return EndUserErasureOut(status="queued", requested_at=requested_at)
