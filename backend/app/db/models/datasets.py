"""Datasets: named collections of evaluation items an experiment replays."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.ids import new_id
from app.db.models.base import Base


class Dataset(Base):
    """A named set of items in a project; the name is unique there. Row-level security applies."""

    __tablename__ = "datasets"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=new_id)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DatasetItem(Base):
    """One case of a dataset. It has no `project_id`: row-level security goes through the dataset.

    `source_trace_id` and `source_span_id` say where a copied item came from; they are plain text
    because the trace may be deleted by retention while the item stays.
    """

    __tablename__ = "dataset_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=new_id)
    dataset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"))
    input: Mapped[Any] = mapped_column(JSONB(none_as_null=True))
    expected_output: Mapped[Any | None] = mapped_column(JSONB(none_as_null=True))
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, server_default=text("'{}'::jsonb")
    )
    source_trace_id: Mapped[str | None] = mapped_column(Text)
    source_span_id: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
