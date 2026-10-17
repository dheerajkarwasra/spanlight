"""Annotation queues: traces waiting for a person to score them."""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Text, func, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.ids import new_id
from app.db.models.base import Base


class QueueItemStatus(StrEnum):
    PENDING = "pending"
    DONE = "done"
    SKIPPED = "skipped"


class ReviewReason(StrEnum):
    """Why an item was queued: by hand, or by the review policy's nightly selection."""

    MANUAL = "manual"
    DOCTOR_FINDING = "doctor_finding"
    ERROR = "error"
    COST_OUTLIER = "cost_outlier"
    RANDOM = "random"


class AnnotationQueue(Base):
    """A named list of traces to review, scored against the score configs it lists.

    `score_config_ids` has no foreign key (it is an array); the API checks it against the
    project when the queue is created. Row-level security applies.
    """

    __tablename__ = "annotation_queues"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=new_id)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(Text)
    instructions: Mapped[str | None] = mapped_column(Text)
    score_config_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AnnotationQueueItem(Base):
    """One trace (or span) in a queue. It has no `project_id`: row-level security goes through
    the queue. `assigned_at` dates the claim, so an abandoned claim can be taken over.

    `reason` says why the item was queued (a `ReviewReason`); `reason_detail` is a JSON object
    of numbers, ids and the trace name behind that reason, never payload text, and is NULL for a
    manual add."""

    __tablename__ = "annotation_queue_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=new_id)
    queue_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("annotation_queues.id", ondelete="CASCADE")
    )
    trace_id: Mapped[str] = mapped_column(Text)
    span_id: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default=text("'pending'"))
    reason: Mapped[str] = mapped_column(Text, server_default=text("'manual'"))
    reason_detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
