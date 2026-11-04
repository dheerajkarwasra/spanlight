"""Versioned repair obligations without trace payloads or subject identifiers."""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, SmallInteger, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base


class AggregateDirtyHour(Base):
    __tablename__ = "aggregate_dirty_hours"

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    bucket_hour: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    generation: Mapped[int] = mapped_column(BigInteger)
    first_marked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )
    pending_consumers: Mapped[int] = mapped_column(SmallInteger, server_default=text("15"))


class AggregateDirtyShard(Base):
    __tablename__ = "aggregate_dirty_shards"

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    bucket_hour: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    shard: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    generation: Mapped[int] = mapped_column(BigInteger)
    first_marked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )
    pending_consumers: Mapped[int] = mapped_column(SmallInteger, server_default=text("15"))
