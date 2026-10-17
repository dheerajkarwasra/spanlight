"""Datasets and their items.

The bodies that create and edit them are owned by the datasets domain (`app.datasets.schemas`),
which validates them; they are re-exported here so routers keep importing from `app.api.schemas`.
An item's `expected_output` is `null` when it has none, never an empty value.
"""

import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import Field

from app.api.schemas.common import ApiModel
from app.datasets.schemas import DatasetItemIn, DatasetPatch, DatasetSpec, ItemPatch
from app.db.models import Dataset, DatasetItem
from app.ingest.schemas import TraceId

__all__ = [
    "DatasetOut",
    "DatasetPatch",
    "DatasetSpec",
    "ItemAddIn",
    "ItemAddedOut",
    "ItemFromTracesIn",
    "ItemOut",
    "ItemPatch",
    "TraceAddedOut",
    "TraceRejectedOut",
    "UploadOut",
    "UploadRejectedOut",
]


class DatasetOut(ApiModel):
    id: uuid.UUID
    name: str
    description: str | None
    item_count: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, dataset: Dataset, item_count: int) -> "DatasetOut":
        return cls(
            id=dataset.id,
            name=dataset.name,
            description=dataset.description,
            item_count=item_count,
            created_at=dataset.created_at,
            updated_at=dataset.updated_at,
        )


class ItemOut(ApiModel):
    id: uuid.UUID
    input: Any
    expected_output: Any | None
    metadata: Annotated[dict[str, Any], Field(validation_alias="metadata_")]
    source_trace_id: str | None
    source_span_id: str | None
    created_at: datetime

    @classmethod
    def of(cls, item: DatasetItem) -> "ItemOut":
        return cls.model_validate(item)


class ItemAddIn(ApiModel):
    items: Annotated[list[DatasetItemIn], Field(min_length=1, max_length=1000)]


class ItemAddedOut(ApiModel):
    accepted: int


class ItemFromTracesIn(ApiModel):
    # Upper-case ids are accepted and lowered, as on every other route that takes trace ids.
    trace_ids: Annotated[list[TraceId], Field(min_length=1, max_length=200)]
    span_id: Annotated[str, Field(min_length=1, max_length=200)] | None = None


class TraceRejectedOut(ApiModel):
    trace_id: str
    reason: str


class TraceAddedOut(ApiModel):
    accepted: int
    rejected: list[TraceRejectedOut]
    # The new items' ids, so a caller can patch the copy (the annotate screen's `metadata.review`).
    item_ids: list[uuid.UUID]


class UploadRejectedOut(ApiModel):
    line: int
    reason: str


class UploadOut(ApiModel):
    accepted: int
    rejected: list[UploadRejectedOut]
