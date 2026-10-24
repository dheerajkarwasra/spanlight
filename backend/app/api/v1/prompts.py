"""Prompts: named templates with immutable versions and movable labels.

Members list and read them (`project:read`); members create prompts, add versions, move labels
and delete (`prompts:write`). A prompt name may contain `/`, so it travels as a path of any
depth; the routes with a suffix come first. A name that is `.` or `..` or starts with `~` is sent
with one leading `~` (see `app.core.wire`). Responses are built before the commit, which ends the
project binding that row-level security needs.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request, Response, status

from app.api.deps import Access, ClockDep, DbSession, client_ip, require
from app.api.locks import lock_project_of
from app.api.prompt_problems import PROMPT_ERRORS, cursor_version, log_prompt, prompt_problem
from app.api.schemas import (
    LabelOut,
    LabelSetIn,
    Page,
    PromptCreate,
    PromptListItemOut,
    PromptOut,
    VersionCreate,
    VersionOut,
    VersionUsageOut,
)
from app.core.errors import not_found
from app.core.pagination import PageLimit, decode_uuid_cursor, encode_cursor
from app.core.permissions import Permission
from app.core.wire import unescape_path_name
from app.prompts import queries, service
from app.prompts.schemas import LabelName
from app.prompts.usage import versions_usage

router = APIRouter(prefix="/projects/{project_id}/prompts", tags=["prompts"])

PromptReader = Annotated[Access, Depends(require(Permission.PROJECT_READ))]
PromptWriter = Annotated[Access, Depends(require(Permission.PROMPTS_WRITE))]

# The wire form of a name: at most 128 characters plus the one-character `~` escape.
WireName = Annotated[str, Path(min_length=1, max_length=129)]
VersionNumber = Annotated[int, Path(ge=1, le=2_147_483_647)]
DEFAULT_PAGE = 50


@router.get("", response_model=Page[PromptListItemOut], summary="List prompts")
async def list_prompts(
    project_id: uuid.UUID,
    access: PromptReader,
    db: DbSession,
    q: Annotated[str | None, Query(max_length=128, description="Name prefix")] = None,
    limit: PageLimit = DEFAULT_PAGE,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> Page[PromptListItemOut]:
    """Most recently changed first, with each prompt's latest version number and labels."""
    after = decode_uuid_cursor(cursor) if cursor else None
    rows = await queries.list_prompts(
        db, access.require_project().id, prefix=q, after=after, limit=limit
    )
    shown = rows[:limit]
    labels = await queries.labels_of(db, [row.prompt.id for row in shown])
    items = [
        PromptListItemOut.of(row, [label for label in labels if label.prompt_id == row.prompt.id])
        for row in shown
    ]
    last = shown[-1].prompt if shown else None
    more = len(rows) > limit and last is not None
    return Page(
        items=items,
        next_cursor=encode_cursor(last.updated_at, str(last.id)) if more and last else None,
    )


@router.post(
    "", status_code=status.HTTP_201_CREATED, response_model=PromptOut, summary="Add a prompt"
)
async def create_prompt(
    project_id: uuid.UUID,
    body: PromptCreate,
    request: Request,
    access: PromptWriter,
    db: DbSession,
    clock: ClockDep,
) -> PromptOut:
    """Store the prompt with its version 1. `409 NAME_TAKEN`; `422 VALIDATION_ERROR` for a
    malformed template."""
    locked_project_id = await lock_project_of(access, db)
    try:
        prompt, version = await service.create_prompt(
            db,
            access.org.id,
            locked_project_id,
            access.user_id,
            body,
            now=clock(),
            ip=client_ip(request),
        )
    except PROMPT_ERRORS as error:
        raise prompt_problem(error) from None
    created = PromptOut.of(prompt, version, [])
    await db.commit()
    log_prompt("prompt_created", access, prompt.id, kind=version.kind.value)
    return created


@router.get(
    "/{name:path}/versions/{version}", response_model=VersionOut, summary="Get a prompt version"
)
async def get_version(
    project_id: uuid.UUID,
    name: WireName,
    version: VersionNumber,
    access: PromptReader,
    db: DbSession,
) -> VersionOut:
    prompt = await queries.get_prompt(db, access.require_project().id, unescape_path_name(name))
    found = await queries.get_version(db, prompt.id, version) if prompt else None
    if found is None:
        raise not_found()
    return VersionOut.of(found)


@router.get(
    "/{name:path}/versions", response_model=Page[VersionOut], summary="List prompt versions"
)
async def list_versions(
    project_id: uuid.UUID,
    name: WireName,
    access: PromptReader,
    db: DbSession,
    clock: ClockDep,
    limit: PageLimit = DEFAULT_PAGE,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    usage: Annotated[bool, Query()] = False,
) -> Page[VersionOut]:
    """Newest first. With `usage=true` each item carries `usage_7d`, read for the page at once."""
    prompt = await queries.get_prompt(db, access.require_project().id, unescape_path_name(name))
    if prompt is None:
        raise not_found()
    rows = await queries.list_versions(db, prompt.id, before=cursor_version(cursor), limit=limit)
    shown = rows[:limit]
    last = shown[-1] if shown else None
    items = [VersionOut.of(version) for version in shown]
    if usage and shown:
        found = await versions_usage(db, prompt.project_id, [v.id for v in shown], clock())
        for item in items:
            item.usage_7d = VersionUsageOut.of(found[item.id])
    return Page(
        items=items,
        next_cursor=(
            encode_cursor(last.created_at, str(last.version))
            if len(rows) > limit and last
            else None
        ),
    )


@router.post(
    "/{name:path}/versions",
    status_code=status.HTTP_201_CREATED,
    response_model=VersionOut,
    summary="Add a prompt version",
)
async def create_version(
    project_id: uuid.UUID,
    name: WireName,
    body: VersionCreate,
    request: Request,
    access: PromptWriter,
    db: DbSession,
    clock: ClockDep,
) -> VersionOut:
    """Add the next version. `409 KIND_MISMATCH` when the kind differs from the prompt's;
    `409 NO_CHANGES` when nothing differs from the latest version; `429 RATE_LIMITED` after 100
    versions of the prompt in a UTC day (`Retry-After` is the time to midnight)."""
    locked_project_id = await lock_project_of(access, db)
    try:
        version = await service.create_version(
            db,
            access.org.id,
            locked_project_id,
            access.user_id,
            unescape_path_name(name),
            body,
            now=clock(),
            ip=client_ip(request),
        )
    except PROMPT_ERRORS as error:
        raise prompt_problem(error) from None
    created = VersionOut.of(version)
    await db.commit()
    log_prompt("prompt_version_created", access, version.prompt_id, version=version.version)
    return created


@router.put(
    "/{name:path}/labels/{label}", response_model=LabelOut, summary="Point a label at a version"
)
async def set_label(
    project_id: uuid.UUID,
    name: WireName,
    label: LabelName,
    body: LabelSetIn,
    request: Request,
    access: PromptWriter,
    db: DbSession,
    clock: ClockDep,
) -> LabelOut:
    """Create the label or move it. `422 LABEL_RESERVED` for `latest`; `404` for a version the
    prompt does not have."""
    locked_project_id = await lock_project_of(access, db)
    try:
        row = await service.set_label(
            db,
            access.org.id,
            locked_project_id,
            access.user_id,
            unescape_path_name(name),
            label,
            body.version,
            now=clock(),
            ip=client_ip(request),
        )
    except PROMPT_ERRORS as error:
        raise prompt_problem(error) from None
    result = LabelOut.of(row)
    await db.commit()
    log_prompt("prompt_label_set", access, row.prompt_id, label=label, version=body.version)
    return result


@router.delete(
    "/{name:path}/labels/{label}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a label",
)
async def delete_label(
    project_id: uuid.UUID,
    name: WireName,
    label: Annotated[str, Path(min_length=1, max_length=64)],
    request: Request,
    access: PromptWriter,
    db: DbSession,
    clock: ClockDep,
) -> Response:
    locked_project_id = await lock_project_of(access, db)
    try:
        await service.delete_label(
            db,
            access.org.id,
            locked_project_id,
            access.user_id,
            unescape_path_name(name),
            label,
            now=clock(),
            ip=client_ip(request),
        )
    except PROMPT_ERRORS as error:
        raise prompt_problem(error) from None
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{name:path}", response_model=PromptOut, summary="Get a prompt")
async def get_prompt(
    project_id: uuid.UUID, name: WireName, access: PromptReader, db: DbSession
) -> PromptOut:
    """The prompt with its latest version and labels."""
    prompt = await queries.get_prompt(db, access.require_project().id, unescape_path_name(name))
    latest = await queries.latest_version(db, prompt.id) if prompt else None
    if prompt is None or latest is None:
        raise not_found()
    return PromptOut.of(prompt, latest, await queries.labels_of(db, [prompt.id]))


@router.delete("/{name:path}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a prompt")
async def delete_prompt(
    project_id: uuid.UUID,
    name: WireName,
    request: Request,
    access: PromptWriter,
    db: DbSession,
) -> Response:
    """Delete the prompt with all its versions and labels."""
    locked_project_id = await lock_project_of(access, db)
    try:
        await service.delete_prompt(
            db,
            access.org.id,
            locked_project_id,
            access.user_id,
            unescape_path_name(name),
            ip=client_ip(request),
        )
    except PROMPT_ERRORS as error:
        raise prompt_problem(error) from None
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
