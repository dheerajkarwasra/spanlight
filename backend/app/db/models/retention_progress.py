"""The retention job's per-class keyset cursors (migration 1035); opportunities, never progress."""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base


class RetentionProgress(Base):
    """One row per fixed cleanup class. `after_scope_id` is the project or billing source served
    last (None for the global classes and an empty roster); the class with the lowest
    `attempt_order` is served next. Global metadata: no tenant column, no row-level security."""

    __tablename__ = "retention_progress"
    __table_args__ = (Index("retention_progress_attempt_order_idx", "attempt_order", "class"),)

    cleanup_class: Mapped[str] = mapped_column("class", Text, primary_key=True)
    attempt_order: Mapped[int] = mapped_column(BigInteger)
    after_scope_id: Mapped[uuid.UUID | None] = mapped_column(UUID)
    last_attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
