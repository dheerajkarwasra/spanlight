"""The Doctor: the insights detectors found in a project, the record of each detector run, and
the explanations Claude wrote for them."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.ids import new_id
from app.db.models.base import Base, _pg_enum
from app.insights.schemas import InsightStatus, Severity


class Insight(Base):
    """One problem in a project, kept up to date by every detection of it.

    Row-level security applies, as for `traces`. `fingerprint` names the problem within the
    project (`app.insights.fingerprint`); the lifecycle lives in `app.insights.service`. The copy
    (title, summary, layer, certainty, fix, verification) is stored so the insight reads the
    same after the catalogue changes; the label is read from the catalogue. `evidence` is the
    latest `Evidence` as JSON (`trace_ids`, `metrics` with decimals as strings, `window`).

    The status decides which columns are set (the migration's CHECKs): `resolved_at` exactly
    when resolved, `muted_until` and `mute_reason` exactly when muted, an acknowledgement
    whenever acknowledged and never while open.
    """

    __tablename__ = "insights"
    __table_args__ = (
        UniqueConstraint("project_id", "fingerprint", name="insights_project_fingerprint_key"),
        # The target of `insight_explanations`' composite key. Migration 0403 adds it.
        UniqueConstraint("id", "project_id", name="insights_id_project_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=new_id)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(Text)
    severity: Mapped[Severity] = mapped_column(_pg_enum(Severity, "insight_severity"))
    status: Mapped[InsightStatus] = mapped_column(_pg_enum(InsightStatus, "insight_status"))
    fingerprint: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text)
    # Values of `app.insights.schemas.FailureLayer` and `Certainty`, checked by the migration.
    failure_layer: Mapped[str] = mapped_column(Text)
    certainty: Mapped[str] = mapped_column(Text)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB)
    suggested_fix: Mapped[str] = mapped_column(Text)
    verification: Mapped[str] = mapped_column(Text)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    occurrences: Mapped[int] = mapped_column(Integer)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    muted_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    mute_reason: Mapped[str | None] = mapped_column(Text)
    acknowledged_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DetectorRun(Base):
    """One detector's pass over a project. Row-level security applies.

    `findings` is how many findings the run returned, NULL exactly when it raised (`error`
    holds the exception class and message, at most 500 characters). `truncated` is true when
    the context the detectors read hit its span cap. Kept for 7 days.
    """

    __tablename__ = "detector_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=new_id)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    detector: Mapped[str] = mapped_column(Text)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    findings: Mapped[int | None] = mapped_column(Integer)
    truncated: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    duration_ms: Mapped[int] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    ran_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class InsightExplanation(Base):
    """Claude's explanation of one insight, or the budget's reservation for one being written.

    Row-level security applies. A reservation (`completed_at` NULL) is inserted with the call's
    worst-case `cost_usd` before the provider is called. It is completed with the answer and the
    real cost; completed without `text` when the provider was (or may have been) paid but gave no
    answer, which keeps the spend in the budget and is never shown; or deleted when the call
    failed unbilled. The cleanup job deletes reservations left behind for more than 10 minutes.
    See `app.insights.explain_service`.
    """

    __tablename__ = "insight_explanations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["insight_id", "project_id"],
            ["insights.id", "insights.project_id"],
            ondelete="CASCADE",
            name="insight_explanations_insight_fkey",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=new_id)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    insight_id: Mapped[uuid.UUID] = mapped_column(UUID)
    model: Mapped[str] = mapped_column(Text)
    text: Mapped[str | None] = mapped_column(Text)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(14, 8))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class InsightFeedback(Base):
    """One member's verdict on one insight: `right` or `wrong`, with an optional note.

    Row-level security applies. One row per user per insight (`insight_feedback_vote_key`); a
    second vote replaces the first. The vote is deleted with its insight or its user. `note` is
    at most 500 characters (migration 0704).
    """

    __tablename__ = "insight_feedback"
    __table_args__ = (
        ForeignKeyConstraint(
            ["insight_id", "project_id"],
            ["insights.id", "insights.project_id"],
            ondelete="CASCADE",
            name="insight_feedback_insight_fkey",
        ),
        UniqueConstraint("project_id", "insight_id", "user_id", name="insight_feedback_vote_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=new_id)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    insight_id: Mapped[uuid.UUID] = mapped_column(UUID)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    verdict: Mapped[str] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
