"""Datasets: named sets of evaluation items.

Members list and read datasets (`project:read`); members create, edit and delete them
(`datasets:write`). Their items are in `dataset_items`. Responses are built before the commit,
which ends the project binding that row-level security needs.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Request, status

from app.api.dataset_deps import (
    DEFAULT_PAGE,
    DatasetReader,
    DatasetWriter,
    log_dataset_event,
    page_of,
)
from app.api.dataset_problems import DATASET_ERRORS, dataset_problem
from app.api.deps import ClockDep, DbSession, client_ip
from app.api.locks import lock_project_of
from app.api.schemas import (
    DatasetOut,
    DatasetPatch,
    DatasetSpec,
    Page,
)
from app.core.errors import not_found
from app.core.pagination import PageLimit, decode_uuid_cursor, encode_cursor
from app.datasets import queries, service

router = APIRouter(prefix="/projects/{project_id}/datasets", tags=["datasets"])


@router.get("", response_model=Page[DatasetOut], summary="List datasets")
async def list_datasets(
    project_id: uuid.UUID,
    access: DatasetReader,
    db: DbSession,
    name: Annotated[str | None, Query(max_length=100, description="Exact name")] = None,
    limit: PageLimit = DEFAULT_PAGE,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> Page[DatasetOut]:
    """Newest first. `name` returns the dataset of that exact name, if any."""
    after = decode_uuid_cursor(cursor)[1] if cursor else None
    rows = await queries.list_datasets(
        db, access.require_project().id, name=name, after=after, limit=limit
    )
    shown, more = page_of(rows, limit)
    last = shown[-1][0] if shown else None
    return Page(
        items=[DatasetOut.of(dataset, count) for dataset, count in shown],
        next_cursor=encode_cursor(last.created_at, str(last.id)) if more and last else None,
    )


@router.post(
    "", status_code=status.HTTP_201_CREATED, response_model=DatasetOut, summary="Add a dataset"
)
async def create_dataset(
    project_id: uuid.UUID,
    body: DatasetSpec,
    request: Request,
    access: DatasetWriter,
    db: DbSession,
    clock: ClockDep,
) -> DatasetOut:
    """Store an empty dataset. `409 NAME_TAKEN`."""
    locked_project_id = await lock_project_of(access, db)
    try:
        dataset = await service.create_dataset(
            db,
            access.org.id,
            locked_project_id,
            access.user_id,
            body,
            now=clock(),
            ip=client_ip(request),
        )
    except DATASET_ERRORS as error:
        raise dataset_problem(error) from None
    created = DatasetOut.of(dataset, 0)
    await db.commit()
    log_dataset_event("dataset_created", access, dataset.id)
    return created


@router.get("/{dataset_id}", response_model=DatasetOut, summary="Get a dataset")
async def get_dataset(
    project_id: uuid.UUID, dataset_id: uuid.UUID, access: DatasetReader, db: DbSession
) -> DatasetOut:
    dataset = await queries.get_dataset(db, access.require_project().id, dataset_id)
    if dataset is None:
        raise not_found()
    return DatasetOut.of(dataset, await queries.count_items(db, dataset.id))


@router.patch("/{dataset_id}", response_model=DatasetOut, summary="Edit a dataset")
async def update_dataset(
    project_id: uuid.UUID,
    dataset_id: uuid.UUID,
    body: DatasetPatch,
    access: DatasetWriter,
    db: DbSession,
    clock: ClockDep,
) -> DatasetOut:
    """Change the description; `null` clears it."""
    try:
        dataset = await service.update_dataset(
            db, access.require_project().id, dataset_id, body, now=clock()
        )
    except DATASET_ERRORS as error:
        raise dataset_problem(error) from None
    updated = DatasetOut.of(dataset, await queries.count_items(db, dataset.id))
    await db.commit()
    return updated


@router.delete("/{dataset_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a dataset")
async def delete_dataset(
    project_id: uuid.UUID,
    dataset_id: uuid.UUID,
    request: Request,
    access: DatasetWriter,
    db: DbSession,
) -> None:
    """Delete the dataset with its items. `409 DATASET_IN_USE` while an experiment uses it."""
    locked_project_id = await lock_project_of(access, db)
    try:
        await service.delete_dataset(
            db,
            access.org.id,
            locked_project_id,
            dataset_id,
            access.user_id,
            ip=client_ip(request),
        )
    except DATASET_ERRORS as error:
        raise dataset_problem(error) from None
    await db.commit()
    log_dataset_event("dataset_deleted", access, dataset_id)
