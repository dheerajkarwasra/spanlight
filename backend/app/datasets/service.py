"""Datasets: create, edit and delete datasets, and add, edit and remove their items. Callers
commit.

Every write runs in a transaction bound to the project (row-level security) and takes the
dataset's row lock first, so two requests cannot both pass the item limit. A locked dataset
(`locked_at` set) refuses every item write and its delete with `DatasetLockedError`; the check
runs under the row lock, so a write either commits before a lock or sees it. Its description can
still change. Audit metadata holds the dataset's name and, for a delete, how many items went with
it; never item content.
"""

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.datasets import queries
from app.datasets.errors import (
    DatasetFullError,
    DatasetInUseError,
    DatasetItemNotFoundError,
    DatasetLockedError,
    DatasetNameTakenError,
    DatasetNotFoundError,
)
from app.datasets.importers import items_from_traces
from app.datasets.schemas import (
    MAX_ITEMS_PER_DATASET,
    DatasetItemIn,
    DatasetPatch,
    DatasetSpec,
    ItemPatch,
    TraceItems,
    TraceRejection,
)
from app.db.errors import violated_constraint
from app.db.models import AuditAction, Dataset, DatasetItem
from app.services.audit import record_audit

NAME_CONSTRAINT = "datasets_project_name_key"


async def create_dataset(
    db: AsyncSession,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    actor_id: uuid.UUID,
    spec: DatasetSpec,
    *,
    now: datetime,
    ip: str | None = None,
) -> Dataset:
    """Store a new dataset. Raises `DatasetNameTakenError`."""
    dataset = Dataset(
        project_id=project_id,
        name=spec.name,
        description=spec.description,
        created_by=actor_id,
        created_at=now,
        updated_at=now,
    )
    db.add(dataset)
    try:
        async with db.begin_nested():
            await db.flush()
    except IntegrityError as error:
        if violated_constraint(error) == NAME_CONSTRAINT:
            raise DatasetNameTakenError from None
        raise
    await record_audit(
        db,
        org_id=org_id,
        actor_user_id=actor_id,
        action=AuditAction.DATASET_CREATE,
        target_type="dataset",
        target_id=dataset.id,
        ip=ip,
        metadata={"name": dataset.name},
    )
    return dataset


async def update_dataset(
    db: AsyncSession,
    project_id: uuid.UUID,
    dataset_id: uuid.UUID,
    patch: DatasetPatch,
    *,
    now: datetime,
) -> Dataset:
    """Change the description. Raises `DatasetNotFoundError`."""
    dataset = await _locked(db, project_id, dataset_id)
    if "description" in patch.model_fields_set and dataset.description != patch.description:
        dataset.description = patch.description
        dataset.updated_at = now
        await db.flush()
    return dataset


async def delete_dataset(
    db: AsyncSession,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    dataset_id: uuid.UUID,
    actor_id: uuid.UUID,
    *,
    ip: str | None = None,
) -> None:
    """Delete the dataset and its items. Raises `DatasetNotFoundError`, `DatasetLockedError`, and
    `DatasetInUseError` while an experiment uses it (the experiments' foreign key refuses the
    delete as well)."""
    dataset = await _writable(db, project_id, dataset_id)
    if await queries.dataset_in_use(db, dataset.id):
        raise DatasetInUseError
    item_count = await queries.count_items(db, dataset.id)
    await db.delete(dataset)
    await db.flush()
    await record_audit(
        db,
        org_id=org_id,
        actor_user_id=actor_id,
        action=AuditAction.DATASET_DELETE,
        target_type="dataset",
        target_id=dataset_id,
        ip=ip,
        metadata={"name": dataset.name, "items": item_count},
    )


async def add_items(
    db: AsyncSession,
    project_id: uuid.UUID,
    dataset_id: uuid.UUID,
    items: list[DatasetItemIn],
    *,
    now: datetime,
) -> list[uuid.UUID]:
    """Store the items and return their ids, in order. All or none: raises
    `DatasetNotFoundError`, `DatasetLockedError` and `DatasetFullError` (the whole batch is
    refused when it would pass the limit)."""
    dataset = await _writable(db, project_id, dataset_id)
    if not items:
        return []
    if await queries.count_items(db, dataset.id) + len(items) > MAX_ITEMS_PER_DATASET:
        raise DatasetFullError(MAX_ITEMS_PER_DATASET)
    ids = await queries.insert_items(db, dataset.id, items)
    dataset.updated_at = now
    await db.flush()
    return ids


async def add_from_traces(
    db: AsyncSession,
    project_id: uuid.UUID,
    dataset_id: uuid.UUID,
    trace_ids: list[str],
    span_id: str | None,
    *,
    now: datetime,
) -> TraceItems:
    """Copy one item from each trace and return what was copied and what was not.

    A trace id outside the project is rejected `trace not found`; a trace and span already in
    this dataset is rejected `already in this dataset`, since a second copy would weigh that case
    twice in every run. `also_in` names the project's other datasets that hold an added trace.
    Raises `DatasetNotFoundError`, `DatasetLockedError` and `DatasetFullError`.
    """
    await _writable(db, project_id, dataset_id)
    unique = list(dict.fromkeys(trace_ids))
    traces = await queries.load_traces(db, project_id, unique, span_id)
    found = items_from_traces(traces, span_id)
    known = {trace.trace_id for trace in traces}
    rejected = [
        TraceRejection(trace_id, "trace not found") for trace_id in unique if trace_id not in known
    ]
    rejected.extend(found.rejected)
    seen = await queries.existing_sources(db, dataset_id, list(known))
    items: list[DatasetItemIn] = []
    for item in found.items:
        source = (item.source_trace_id or "", item.source_span_id)
        if source in seen:
            rejected.append(TraceRejection(source[0], "already in this dataset"))
            continue
        seen.add(source)
        items.append(item)
    item_ids = await add_items(db, project_id, dataset_id, items, now=now)
    added = [item.source_trace_id or "" for item in items]
    also_in = await queries.other_datasets_with(db, dataset_id, added) if added else {}
    return TraceItems(items=items, rejected=rejected, item_ids=item_ids, also_in=also_in)


async def update_item(
    db: AsyncSession,
    project_id: uuid.UUID,
    dataset_id: uuid.UUID,
    item_id: uuid.UUID,
    patch: ItemPatch,
) -> DatasetItem:
    """Change the fields sent. Raises `DatasetNotFoundError`, `DatasetLockedError` and
    `DatasetItemNotFoundError`."""
    await _writable(db, project_id, dataset_id)
    item = await queries.get_item(db, dataset_id, item_id)
    if item is None:
        raise DatasetItemNotFoundError
    if "expected_output" in patch.model_fields_set:
        item.expected_output = patch.expected_output
    if "metadata" in patch.model_fields_set and patch.metadata is not None:
        item.metadata_ = patch.metadata
    await db.flush()
    return item


async def delete_item(
    db: AsyncSession, project_id: uuid.UUID, dataset_id: uuid.UUID, item_id: uuid.UUID
) -> None:
    """Remove one item. Raises `DatasetNotFoundError`, `DatasetLockedError` and
    `DatasetItemNotFoundError`."""
    await _writable(db, project_id, dataset_id)
    if not await queries.delete_item(db, dataset_id, item_id):
        raise DatasetItemNotFoundError


async def project_dataset(
    db: AsyncSession, project_id: uuid.UUID, dataset_id: uuid.UUID
) -> Dataset | None:
    """The project's dataset with this id, for callers outside datasets (experiments)."""
    return await queries.get_dataset(db, project_id, dataset_id)


async def item_ids(db: AsyncSession, dataset_id: uuid.UUID, *, limit: int) -> list[uuid.UUID]:
    """The dataset's first `limit` item ids in id order: the items an experiment run scores."""
    return await queries.item_ids(db, dataset_id, limit=limit)


async def items_by_ids(
    db: AsyncSession, dataset_id: uuid.UUID, ids: Sequence[uuid.UUID]
) -> list[DatasetItem]:
    """The dataset's items with these ids in id order; an item deleted since is skipped."""
    return await queries.items_by_ids(db, dataset_id, ids)


async def _locked(db: AsyncSession, project_id: uuid.UUID, dataset_id: uuid.UUID) -> Dataset:
    dataset = await queries.get_dataset(db, project_id, dataset_id, lock=True)
    if dataset is None:
        raise DatasetNotFoundError
    return dataset


async def _writable(db: AsyncSession, project_id: uuid.UUID, dataset_id: uuid.UUID) -> Dataset:
    dataset = await _locked(db, project_id, dataset_id)
    if dataset.locked_at is not None:
        raise DatasetLockedError
    return dataset
