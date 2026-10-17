"""Review policies and their nightly selections (migration 1001)."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, Text, func, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.ids import new_id
from app.db.models.base import Base


class ReviewPolicy(Base):
    """What a project queues for review every UTC night; one per project. Row-level security
    applies.

    `queue_id` has no cascade: the queue a policy fills cannot be deleted while the policy
    exists (`409 QUEUE_IN_USE`). The service checks the queue belongs to the project. An empty
    `environments` means every non-internal environment.
    """

    __tablename__ = "review_policies"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=new_id)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    queue_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("annotation_queues.id"))
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    random_rate: Mapped[Decimal] = mapped_column(Numeric(5, 4), server_default=text("0.01"))
    include_errors: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    include_cost_outliers: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    include_doctor_findings: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    daily_cap: Mapped[int] = mapped_column(Integer, server_default=text("50"))
    environments: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ReviewSelection(Base):
    """The record of one night's selection for a project and UTC day. Row-level security
    applies.

    `queue_id` becomes NULL when the queue is deleted, so the history survives it. `candidates`
    and `queued` map each reason to a count; `duration_ms` is NULL until the row is completed.
    """

    __tablename__ = "review_selections"

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    queue_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("annotation_queues.id", ondelete="SET NULL")
    )
    candidates: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    queued: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    skipped_duplicates: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    cap_hit: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    ran_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
