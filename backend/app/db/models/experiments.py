"""Experiments, their runs and the items a run stored (migration 0506)."""

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, Integer, Numeric, Text, func, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.ids import new_id
from app.db.models.base import Base


class ExperimentRunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class Experiment(Base):
    """A dataset replayed through one target and scored by the attached evaluators. The name is
    unique in the project. Row-level security applies.

    `config` is the validated `app.evals.experiment_config.ExperimentConfig`. `evaluator_ids` are
    not foreign keys: an evaluator deleted later is skipped. The dataset cannot be deleted while
    an experiment uses it.
    """

    __tablename__ = "experiments"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=new_id)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(Text)
    dataset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("datasets.id", ondelete="NO ACTION"))
    config: Mapped[dict[str, Any]] = mapped_column(JSONB)
    evaluator_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID), server_default=text("'{}'"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ExperimentRun(Base):
    """One run of an experiment. No `project_id`: row-level security goes through the experiment.

    At most one run per experiment is `queued` or `running` (a partial unique index). `totals` is
    written when the run ends; `heartbeat_at` lets the reaper fail a run whose worker died.
    """

    __tablename__ = "experiment_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=new_id)
    experiment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("experiments.id", ondelete="CASCADE")
    )
    # 1, 2, 3… per experiment, assigned under the experiment's row lock when the run is queued.
    number: Mapped[int] = mapped_column(Integer)
    # The effective target: the experiment's, or the same prompt at the version of an override.
    target: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(Text, server_default=text("'queued'"))
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    totals: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    started_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )


class ExperimentItem(Base):
    """What one dataset item gave in one run. Row-level security goes through the run.

    `dataset_item_id` and `trace_id` are plain values: the item and the trace may be deleted while
    this record stays. `output` is `{text, finish_reason}`; `scores` is `{name: value}`.
    `cost_usd` is None when unknown, never 0.

    `input`, `expected_output` and `item_metadata` copy the dataset item as it was when this run
    scored it, so later edits to the item never change what the run means. `input` None means the
    row predates copies (a legacy row); otherwise `input` and `item_metadata` are both set.
    """

    __tablename__ = "experiment_items"

    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("experiment_runs.id", ondelete="CASCADE"), primary_key=True
    )
    dataset_item_id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True)
    trace_id: Mapped[str | None] = mapped_column(Text)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    input: Mapped[Any | None] = mapped_column(JSONB(none_as_null=True))
    expected_output: Mapped[Any | None] = mapped_column(JSONB(none_as_null=True))
    item_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    scores: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    latency_ms: Mapped[float | None] = mapped_column(Float)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(14, 8))
    error: Mapped[str | None] = mapped_column(Text)
    # Scoring the answer broke; the answer and its cost are kept.
    evaluator_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
