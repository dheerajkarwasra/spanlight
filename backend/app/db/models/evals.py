"""Evaluators and their runs: automatic scoring of traces (migration 0505)."""

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.ids import new_id
from app.db.models.base import Base


class EvaluatorKind(StrEnum):
    HEURISTIC = "heuristic"
    LLM_JUDGE = "llm_judge"
    WEBHOOK = "webhook"


class EvaluatorRunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class EvaluatorRunSource(StrEnum):
    """Why a run exists: sampled online, a test from the dashboard, or an experiment item."""

    ONLINE = "online"
    TEST = "test"
    EXPERIMENT = "experiment"


class Evaluator(Base):
    """An automatic scorer in a project; the name is unique there. Row-level security applies.

    `config` is the validated discriminated union of `app.evals.config` and its `kind` always
    equals the `kind` column (CHECK). Judges must have a positive `monthly_budget_usd`.
    """

    __tablename__ = "evaluators"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=new_id)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB)
    sampling_rate: Mapped[Decimal] = mapped_column(Numeric(5, 4), server_default=text("0"))
    filters: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    monthly_budget_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EvaluatorRun(Base):
    """One evaluation of a trace (or span). No `project_id`: row-level security goes through the
    evaluator.

    `reserved_usd` holds a judge's estimated cost while the call runs; `cost_usd` is what the run
    is charged once settled. `heartbeat_at` lets the reaper fail runs whose worker died. Only
    `online` runs are unique per evaluator and target (a partial unique index).
    """

    __tablename__ = "evaluator_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=new_id)
    evaluator_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("evaluators.id", ondelete="CASCADE"))
    trace_id: Mapped[str] = mapped_column(Text)
    span_id: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default=text("'queued'"))
    source: Mapped[str] = mapped_column(Text, server_default=text("'online'"))
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(14, 8), server_default=text("0"))
    reserved_usd: Mapped[Decimal] = mapped_column(Numeric(14, 8), server_default=text("0"))
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
