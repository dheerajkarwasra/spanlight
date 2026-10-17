"""Replay runs: the per-project monthly budget ledger for replayed requests (migration 1002)."""

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import DateTime, ForeignKey, Numeric, Text, text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.ids import new_id
from app.db.models.base import Base


class ReplayStatus(StrEnum):
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class ReplayFidelity(StrEnum):
    """How close the sent body is to the stored one: untouched, edited, or rebuilt from the
    messages alone."""

    EXACT = "exact"
    MODIFIED = "modified"
    MESSAGES_ONLY = "messages_only"


class ReplayRun(Base):
    """One replay of a stored request and its place on the project's monthly budget. Row-level
    security applies.

    The source and result ids are plain text, not foreign keys: retention may delete the spans
    while the run stays on the ledger. `reserved_usd` holds the estimate while the call runs;
    `cost_usd` is what the run is charged once settled. `heartbeat_at` lets the reaper fail runs
    whose worker died. `error` is a run-level string (≤ 500 characters), never a provider body.
    """

    __tablename__ = "replay_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=new_id)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    source_trace_id: Mapped[str] = mapped_column(Text)
    source_span_id: Mapped[str] = mapped_column(Text)
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    credential_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("provider_credentials.id", ondelete="SET NULL")
    )
    model: Mapped[str] = mapped_column(Text)
    fidelity: Mapped[str] = mapped_column(Text)
    changed: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    status: Mapped[str] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    reserved_usd: Mapped[Decimal] = mapped_column(Numeric(14, 8), server_default=text("0"))
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(14, 8), server_default=text("0"))
    result_trace_id: Mapped[str | None] = mapped_column(Text)
    result_span_id: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
