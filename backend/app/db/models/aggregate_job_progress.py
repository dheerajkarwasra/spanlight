"""Durable scheduling cursors; admission never certifies source completeness."""

import uuid
from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, SmallInteger, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base


class AggregateJobProgress(Base):
    __tablename__ = "aggregate_job_progress"

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    kind: Mapped[str] = mapped_column(Text, primary_key=True)
    attempt_order: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    last_attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_period: Mapped[date | None] = mapped_column(Date)
    settled_period: Mapped[date | None] = mapped_column(Date)
    active_period: Mapped[date | None] = mapped_column(Date)
    next_day: Mapped[date | None] = mapped_column(Date)
    active_outcomes: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"))
    settled_outcomes: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"))
    expired_through_period: Mapped[date | None] = mapped_column(Date)
    dispatch_number: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    queued_key: Mapped[str | None] = mapped_column(Text)
    admitted_hour: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_closed_hour: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_pending_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
