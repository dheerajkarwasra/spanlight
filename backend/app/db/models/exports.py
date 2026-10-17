"""Trace exports: a request to write filtered traces to a file in object storage."""

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.ids import new_id
from app.db.models.base import Base, _pg_enum


class ExportKind(enum.StrEnum):
    TRACES = "traces"
    ATTRIBUTION = "attribution"


class ExportFormat(enum.StrEnum):
    JSONL = "jsonl"
    CSV = "csv"


class ExportStatus(enum.StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    EXPIRED = "expired"


class Export(Base):
    """One export request and its progress. Row-level security applies, as for `traces`.

    The migration has the CHECK constraints: a `done` row always has its file's key, size, row
    count and times, and a `failed` row always has an `error_code`.
    """

    __tablename__ = "exports"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=new_id)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    kind: Mapped[ExportKind] = mapped_column(
        _pg_enum(ExportKind, "export_kind"), server_default=text("'traces'")
    )
    format: Mapped[ExportFormat] = mapped_column(_pg_enum(ExportFormat, "export_format"))
    # The trace-list filters as validated at request time, with `from` and `to` as ISO strings.
    filters: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[ExportStatus] = mapped_column(
        _pg_enum(ExportStatus, "export_status"), server_default=text("'queued'")
    )
    storage_key: Mapped[str | None] = mapped_column(Text)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    row_count: Mapped[int | None] = mapped_column(BigInteger)
    error_code: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
