"""Alerts: the channels an organization's alerts go to, and the rules, states and events of a
project's alerts."""

import enum
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
    LargeBinary,
    Numeric,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.alerts.types import AlertRuleKind, Comparator, Metric, RuleStateName
from app.core.ids import new_id
from app.db.models.base import Base, _pg_enum


class AlertChannelKind(enum.StrEnum):
    EMAIL = "email"
    SLACK = "slack"
    WEBHOOK = "webhook"
    PAGERDUTY = "pagerduty"


class AlertChannel(Base):
    """Where an organization's alerts go: email recipients, Slack, a webhook or PagerDuty.

    Scoped to the organization and not under row-level security: every query filters by
    `org_id`. `config` holds the kind's non-secret settings (`app.alerts.schemas`). The secret
    (the Slack URL, the webhook signing secret, the PagerDuty routing key) is sealed with
    `app.core.crypto`: `secret_enc` and `secret_key_id` together are a `Sealed` value, both NULL
    for an email channel. The clear secret is never stored and never returned by the API, apart
    from a webhook secret once, when it is generated.
    """

    __tablename__ = "alert_channels"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=new_id)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    kind: Mapped[AlertChannelKind] = mapped_column(_pg_enum(AlertChannelKind, "alert_channel_kind"))
    name: Mapped[str] = mapped_column(Text)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB)
    secret_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    secret_key_id: Mapped[str | None] = mapped_column(Text)
    # When a test notification last went through; NULL until one does.
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    @property
    def has_secret(self) -> bool:
        return self.secret_enc is not None

    def __repr__(self) -> str:
        # The default repr would include the sealed bytes and the recipients; ids are enough.
        return f"AlertChannel(id={self.id!s}, org_id={self.org_id!s}, kind={self.kind.value})"


class AlertRule(Base):
    """What a project watches: one metric over a trailing window, compared with a line.

    Row-level security applies, as for `traces`. The kind decides which columns are set (the
    migration's CHECKs): `threshold` rules have a `threshold`; `anomaly` rules have
    `baseline_windows` and `sensitivity` instead; `budget` rules (owned by a budget, hidden from
    the rules API) read `spend` over the budget's period, so `window_minutes` is NULL.
    `filters` is a JSON object of exact-match filters. `channel_ids` name channels of the
    organization without a foreign key; evaluation skips an id that no longer resolves.
    """

    __tablename__ = "alert_rules"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=new_id)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(Text)
    kind: Mapped[AlertRuleKind] = mapped_column(_pg_enum(AlertRuleKind, "alert_rule_kind"))
    metric: Mapped[Metric] = mapped_column(_pg_enum(Metric, "alert_metric"))
    filters: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'"))
    window_minutes: Mapped[int | None] = mapped_column(Integer)
    comparator: Mapped[Comparator] = mapped_column(_pg_enum(Comparator, "alert_comparator"))
    threshold: Mapped[Decimal | None] = mapped_column(Numeric)
    baseline_windows: Mapped[int | None] = mapped_column(Integer)
    sensitivity: Mapped[Decimal | None] = mapped_column(Numeric)
    cooldown_minutes: Mapped[int] = mapped_column(Integer, server_default=text("15"))
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    channel_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), server_default=text("'{}'")
    )
    # While in the future, the rule still records events but notifies nobody.
    muted_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AlertState(Base):
    """A rule's current state, overwritten by every evaluation. Row-level security applies.

    `since` is when the rule entered `state`; for `ok` after a resolve, the resolve time.
    `last_value` is the metric's value at `last_evaluated_at` (NULL when it was unknown).
    `clear_since` is when a firing rule's value first read clear in its recovery window; NULL
    otherwise (`app.alerts.evaluate.clear_since`).
    `project_id` repeats the rule's so the policy needs no join; the composite foreign key keeps
    the two equal.
    """

    __tablename__ = "alert_states"
    __table_args__ = (
        ForeignKeyConstraint(
            ["rule_id", "project_id"],
            ["alert_rules.id", "alert_rules.project_id"],
            ondelete="CASCADE",
            name="alert_states_rule_fkey",
        ),
    )

    rule_id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    state: Mapped[RuleStateName] = mapped_column(_pg_enum(RuleStateName, "alert_rule_state"))
    since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_value: Mapped[Decimal | None] = mapped_column(Numeric)
    last_evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    clear_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AlertEvent(Base):
    """One firing episode of a rule, from `started_at` until `resolved_at` (NULL while open).

    Row-level security applies. A partial unique index allows one open event per rule. `value`
    and `threshold` are the ones that fired it (an anomaly rule's threshold is the one computed
    then). `payload` is the `AlertPayload` the fire sent. `state` is the state recorded when the
    event was opened.
    """

    __tablename__ = "alert_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["rule_id", "project_id"],
            ["alert_rules.id", "alert_rules.project_id"],
            ondelete="CASCADE",
            name="alert_events_rule_fkey",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=new_id)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    rule_id: Mapped[uuid.UUID] = mapped_column(UUID)
    state: Mapped[RuleStateName] = mapped_column(_pg_enum(RuleStateName, "alert_rule_state"))
    value: Mapped[Decimal | None] = mapped_column(Numeric)
    threshold: Mapped[Decimal | None] = mapped_column(Numeric)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'"))
