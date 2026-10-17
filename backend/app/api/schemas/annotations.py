"""Annotation queues and their items.

The bodies that create queues, queue traces and complete items are owned by the annotations
domain (`app.annotations.schemas`), which validates them; they are re-exported here so routers
import from one place.
"""

import uuid
from datetime import datetime
from typing import Any

from app.annotations.queries import QueueProgress
from app.annotations.schemas import CompletedScore, Completion, ItemsIn, QueueSpec
from app.api.schemas.common import ApiModel
from app.db.models import AnnotationQueue, AnnotationQueueItem

__all__ = [
    "CompletedScore",
    "Completion",
    "ItemsAddedOut",
    "ItemsIn",
    "QueueItemOut",
    "QueueOut",
    "QueueProgressOut",
    "QueueSpec",
    "ReasonProgressOut",
]


class ReasonProgressOut(ApiModel):
    pending: int
    done: int
    skipped: int


class QueueProgressOut(ApiModel):
    """Counts over the whole queue, then per reason; every reason is present, zeros included."""

    pending: int
    done: int
    skipped: int
    by_reason: dict[str, ReasonProgressOut]


class QueueOut(ApiModel):
    id: uuid.UUID
    name: str
    instructions: str | None
    score_config_ids: list[uuid.UUID]
    progress: QueueProgressOut
    created_at: datetime

    @classmethod
    def of(cls, queue: AnnotationQueue, progress: QueueProgress) -> "QueueOut":
        return cls(
            id=queue.id,
            name=queue.name,
            instructions=queue.instructions,
            score_config_ids=queue.score_config_ids,
            progress=QueueProgressOut(
                pending=progress.total["pending"],
                done=progress.total["done"],
                skipped=progress.total["skipped"],
                by_reason={
                    reason: ReasonProgressOut(**counts)
                    for reason, counts in progress.by_reason.items()
                },
            ),
            created_at=queue.created_at,
        )


class QueueItemOut(ApiModel):
    id: uuid.UUID
    trace_id: str
    span_id: str | None
    status: str
    reason: str
    reason_detail: dict[str, Any] | None
    assigned_to: uuid.UUID | None
    assigned_at: datetime | None
    completed_by: uuid.UUID | None
    completed_at: datetime | None
    created_at: datetime

    @classmethod
    def of(cls, item: AnnotationQueueItem) -> "QueueItemOut":
        return cls.model_validate(item)


class ItemsAddedOut(ApiModel):
    added: int
    skipped_duplicates: int
