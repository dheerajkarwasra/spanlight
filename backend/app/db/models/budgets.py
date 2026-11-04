"""Budgets: a project's spend caps, each evaluated by a hidden alert rule it owns."""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Numeric,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.budgets.types import BudgetAction, BudgetPeriod, BudgetScope
from app.core.ids import new_id
from app.db.models.base import Base, _pg_enum


class Budget(Base):
    """A cap on the spend of one scope over a UTC day or month. Row-level security applies.

    `scope_id` is NULL exactly for a `project` budget; otherwise it holds the gateway key id, the
    end user's `external_user_id` or the model, as text. `rule_id` is the hidden `budget` alert
    rule that measures the spend; the budget and its rule are written together, and the rule's
    state is the budget's state. `channel_ids` mirrors the rule's.
    """

    __tablename__ = "budgets"
    __table_args__ = (
        ForeignKeyConstraint(
            ["rule_id", "project_id"],
            ["alert_rules.id", "alert_rules.project_id"],
            ondelete="CASCADE",
            name="budgets_rule_fkey",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=new_id)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(Text)
    scope: Mapped[BudgetScope] = mapped_column(_pg_enum(BudgetScope, "budget_scope"))
    scope_id: Mapped[str | None] = mapped_column(Text)
    period: Mapped[BudgetPeriod] = mapped_column(_pg_enum(BudgetPeriod, "budget_period"))
    amount_usd: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    action: Mapped[BudgetAction] = mapped_column(_pg_enum(BudgetAction, "budget_action"))
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    channel_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), server_default=text("'{}'")
    )
    rule_id: Mapped[uuid.UUID] = mapped_column(UUID, unique=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BudgetCounter(Base):
    """One budget's gateway spend this period: what settled and what is reserved in flight.

    The gateway reserves an estimate before forwarding a call and settles the real cost with
    the span (ADR 0017). One hot row per (budget, period); `period_start` is the budget's UTC
    day or month. Row-level security applies.
    """

    __tablename__ = "budget_counters"

    budget_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("budgets.id", ondelete="CASCADE"), primary_key=True
    )
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    settled_usd: Mapped[Decimal] = mapped_column(Numeric(18, 8), server_default=text("0"))
    legacy_unattributed_usd: Mapped[Decimal] = mapped_column(
        Numeric(18, 8), server_default=text("0")
    )
    reserved_usd: Mapped[Decimal] = mapped_column(Numeric(18, 8), server_default=text("0"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BudgetReservation(Base):
    """A gateway call's estimate held against one budget until its span settles it.

    `held` until the span write settles it; `reaped` once the worker has counted an expired
    one at its estimate. `settled` retains identity while raw spans exist and for at least
    90 days from admission, so a fresh history read cannot double-count it. No payload or scope
    metadata is copied here. RLS applies.
    """

    __tablename__ = "budget_reservations"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=new_id)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    budget_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("budgets.id", ondelete="CASCADE"))
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    trace_id: Mapped[str] = mapped_column(Text)
    span_id: Mapped[str] = mapped_column(Text)
    amount_usd: Mapped[Decimal] = mapped_column(Numeric(18, 8))
    reclassified: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    charged_usd: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    status: Mapped[str] = mapped_column(Text, server_default=text("'held'"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class BudgetDeletedSpan(Base):
    """A deleted priced span of the current UTC month, kept so budget admission still counts it.

    Written by the `spans` delete trigger (migration 1031), read by `app.budgets.history` and
    pruned with the counters. Only ids, time, money, model and the gateway key: no user
    identifier, trace name, metadata or payload. RLS applies.
    """

    __tablename__ = "budget_deleted_spans"

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    trace_id: Mapped[str] = mapped_column(Text, primary_key=True)
    span_id: Mapped[str] = mapped_column(Text, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(18, 8))
    model: Mapped[str | None] = mapped_column(Text)
    source_key_id: Mapped[uuid.UUID | None] = mapped_column(UUID)
    deleted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BudgetBucketVersion(Base):
    """The generation of one UTC hour of a project's budget inputs (migration 1032).

    Statement triggers on `spans` and `budget_reservations` bump it in the writer's own
    transaction; a `BudgetBucketSnapshot` is valid while its generation matches. Pruned with
    the counters. RLS applies.
    """

    __tablename__ = "budget_bucket_versions"

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    bucket_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    generation: Mapped[int] = mapped_column(BigInteger)


class BudgetBucketSnapshot(Base):
    """One budget's committed spend of one UTC hour, as `app.budgets.compaction` computed it
    against `generation` (0: no version row existed; -1: created by a compactor, never
    computed) plus the deltas folded in since. A cache: deleting rows is the repair. RLS
    applies.
    """

    __tablename__ = "budget_bucket_snapshots"

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    budget_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("budgets.id", ondelete="CASCADE"), primary_key=True
    )
    bucket_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    committed_usd: Mapped[Decimal] = mapped_column(Numeric(18, 8))
    generation: Mapped[int] = mapped_column(BigInteger)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class BudgetBucketDelta(Base):
    """An exact change to one budget's spend of one UTC hour that a writer appended (migration
    1033): an uncorrelated span stored or corrected, a settled charge. Folded into the hour's
    `BudgetBucketSnapshot` under its row lock by admission or the reaper's job. RLS applies.
    """

    __tablename__ = "budget_bucket_deltas"

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    budget_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("budgets.id", ondelete="CASCADE"), primary_key=True
    )
    bucket_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    seq: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("nextval('budget_bucket_delta_seq')")
    )
    amount_usd: Mapped[Decimal] = mapped_column(Numeric(18, 8))
