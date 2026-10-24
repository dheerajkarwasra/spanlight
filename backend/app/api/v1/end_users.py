"""End-user analytics: the users of a project and the detail of one.

The routes only authorize and serialize. What the figures mean is in ``app.end_users.service``.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from app.api.deps import DbSession, ReadAccess, require
from app.api.params import NO_NUL, Environment
from app.api.schemas import UserDetailOut, UserListOut
from app.api.window import Window
from app.core.pagination import DEFAULT_PAGE_SIZE, PageLimit
from app.core.permissions import Permission
from app.end_users import service
from app.end_users.queries import UserSort
from app.explorer.trace_sort import SortOrder

router = APIRouter(prefix="/projects/{project_id}/users", tags=["users"])

# Also readable by an API key with `traces:read`, for the project it belongs to.
ProjectReader = Annotated[ReadAccess, Depends(require(Permission.PROJECT_READ, allow_api_key=True))]
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
