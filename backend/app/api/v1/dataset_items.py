"""Dataset items: list, add (JSON, from traces or a file upload), edit and delete.

Members list items (`project:read`); members change them (`datasets:write`). Responses are built
before the commit, which ends the project binding that row-level security needs.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, UploadFile, status
from fastapi.concurrency import run_in_threadpool

from app.api.dataset_deps import (
    DEFAULT_PAGE,
    DatasetReader,
    DatasetWriter,
    log_dataset_event,
    page_of,
)
from app.api.dataset_problems import DATASET_ERRORS, dataset_problem
from app.api.deps import ClockDep, DbSession
from app.api.schemas import (
    ItemAddedOut,
    ItemAddIn,
    ItemFromTracesIn,
    ItemOut,
    ItemPatch,
    Page,
    TraceAddedOut,
    TraceRejectedOut,
    UploadOut,
    UploadRejectedOut,
)
from app.core.errors import ProblemError, not_found
from app.core.pagination import PageLimit, decode_uuid_cursor, encode_cursor
from app.datasets import queries, service
from app.datasets.errors import UnsupportedFileError
from app.datasets.importers import parse_upload
from app.datasets.schemas import MAX_UPLOAD_BYTES, ParsedItems

router = APIRouter(prefix="/projects/{project_id}/datasets", tags=["datasets"])


@router.get("/{dataset_id}/items", response_model=Page[ItemOut], summary="List a dataset's items")
async def list_items(
    project_id: uuid.UUID,
    dataset_id: uuid.UUID,
    access: DatasetReader,
    db: DbSession,
    limit: PageLimit = DEFAULT_PAGE,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> Page[ItemOut]:
    """By id, oldest first."""
    if await queries.get_dataset(db, access.require_project().id, dataset_id) is None:
        raise not_found()
    after = decode_uuid_cursor(cursor)[1] if cursor else None
    rows = await queries.list_items(db, dataset_id, after=after, limit=limit)
    shown, more = page_of(rows, limit)
    last = shown[-1] if shown else None
    return Page(
        items=[ItemOut.of(item) for item in shown],
        next_cursor=encode_cursor(last.created_at, str(last.id)) if more and last else None,
    )


@router.post(
    "/{dataset_id}/items",
    status_code=status.HTTP_201_CREATED,
    response_model=ItemAddedOut,
    summary="Add items",
)
async def add_items(
    project_id: uuid.UUID,
    dataset_id: uuid.UUID,
    body: ItemAddIn,
    access: DatasetWriter,
    db: DbSession,
    clock: ClockDep,
) -> ItemAddedOut:
    """Add up to 1 000 items. `422 DATASET_FULL` past 10 000 items; nothing is stored then."""
    try:
        accepted = await service.add_items(
            db, access.require_project().id, dataset_id, body.items, now=clock()
        )
    except DATASET_ERRORS as error:
        raise dataset_problem(error) from None
    await db.commit()
    log_dataset_event("dataset_items_added", access, dataset_id, accepted=accepted)
    return ItemAddedOut(accepted=accepted)


@router.post(
    "/{dataset_id}/items/from-traces",
    status_code=status.HTTP_201_CREATED,
    response_model=TraceAddedOut,
    summary="Add items from traces",
)
async def add_items_from_traces(
    project_id: uuid.UUID,
    dataset_id: uuid.UUID,
    body: ItemFromTracesIn,
    access: DatasetWriter,
    db: DbSession,
    clock: ClockDep,
) -> TraceAddedOut:
    """Copy one item from each of up to 200 traces: the span `span_id`, else the trace's first
    `llm` span. A trace that gives no item is listed in `rejected` with the reason (`trace not
    found`, `no llm span`, `no payload stored`). The recorded output is only a starting point for
    the expected output."""
    try:
        result = await service.add_from_traces(
            db,
            access.require_project().id,
            dataset_id,
            body.trace_ids,
            body.span_id,
            now=clock(),
        )
    except DATASET_ERRORS as error:
        raise dataset_problem(error) from None
    await db.commit()
    log_dataset_event("dataset_items_added", access, dataset_id, accepted=len(result.items))
    return TraceAddedOut(
        accepted=len(result.items),
        rejected=[TraceRejectedOut(trace_id=r.trace_id, reason=r.reason) for r in result.rejected],
    )


@router.post("/{dataset_id}/items/upload", response_model=UploadOut, summary="Upload items")
async def upload_items(
    project_id: uuid.UUID,
    dataset_id: uuid.UUID,
    file: UploadFile,
    access: DatasetWriter,
    db: DbSession,
    clock: ClockDep,
) -> UploadOut:
    """Add the items of a `.jsonl` or `.csv` file of UTF-8 text, at most 5 MB. A bad line is
    listed in `rejected` with its line number and the rest is accepted. `415 UNSUPPORTED_FILE`
    for another extension or encoding, `413 PAYLOAD_TOO_LARGE` past 5 MB."""
    try:
        parsed = await _parse_upload(file)
        accepted = await service.add_items(
            db, access.require_project().id, dataset_id, parsed.items, now=clock()
        )
    except DATASET_ERRORS as error:
        raise dataset_problem(error) from None
    await db.commit()
    log_dataset_event(
        "dataset_items_added", access, dataset_id, accepted=accepted, rejected=len(parsed.errors)
    )
    return UploadOut(
        accepted=accepted,
        rejected=[UploadRejectedOut(line=e.line, reason=e.reason) for e in parsed.errors],
    )


async def _parse_upload(file: UploadFile) -> ParsedItems:
    name = file.filename or ""
    if not name.lower().endswith((".jsonl", ".csv")):
        raise UnsupportedFileError("upload a .jsonl or .csv file")
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise ProblemError(413, "PAYLOAD_TOO_LARGE", "The file is larger than 5 MB.")
    # Parsing 5 MB is CPU work that would otherwise stall the event loop for every tenant.
    return await run_in_threadpool(parse_upload, name, data)


@router.patch("/{dataset_id}/items/{item_id}", response_model=ItemOut, summary="Edit an item")
async def update_item(
    project_id: uuid.UUID,
    dataset_id: uuid.UUID,
    item_id: uuid.UUID,
    body: ItemPatch,
    access: DatasetWriter,
    db: DbSession,
) -> ItemOut:
    """Change `expected_output` (`null` clears it) and `metadata` (replaced as a whole)."""
    try:
        item = await service.update_item(db, access.require_project().id, dataset_id, item_id, body)
    except DATASET_ERRORS as error:
        raise dataset_problem(error) from None
    updated = ItemOut.of(item)
    await db.commit()
    return updated


@router.delete(
    "/{dataset_id}/items/{item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an item",
)
async def delete_item(
    project_id: uuid.UUID,
    dataset_id: uuid.UUID,
    item_id: uuid.UUID,
    access: DatasetWriter,
    db: DbSession,
) -> None:
    try:
        await service.delete_item(db, access.require_project().id, dataset_id, item_id)
    except DATASET_ERRORS as error:
        raise dataset_problem(error) from None
    await db.commit()
