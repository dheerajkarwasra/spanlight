"""Billing sources: a provider account's cost lines, fetched or uploaded, for reconciliation.

All four tables are scoped to an organization and not under row-level security: every query
filters by `org_id`, as `provider_credentials` does. The value sets below mirror the CHECKs of
migration 0601; the database, not the application, is what refuses a value outside them.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
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
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.ids import new_id
from app.db.models.base import Base
from app.pricing.tiers import ServiceTier

BillingSourceKind = Literal["anthropic_admin_api", "openai_costs_api", "csv_upload"]
BillingProvider = Literal["anthropic", "openai", "google"]
SourceStatus = Literal["never_fetched", "ok", "failing"]
FetchTrigger = Literal["scheduled", "manual", "csv_upload"]
RunStatus = Literal["running", "ok", "failed"]
LineKind = Literal["tokens", "web_search", "code_execution", "credit", "other"]
TokenType = Literal[
    "uncached_input",
    "cache_read",
    "cache_write_5m",
    "cache_write_1h",
    "output",
    "input",
    "cached_input",
]


class BillingSource(Base):
    """One provider account an organization reconciles against.

    An API kind (`anthropic_admin_api`, `openai_costs_api`) holds the admin key sealed with
    `app.core.crypto`: `ciphertext` and `key_id` together are a `Sealed` value, set together or
    not at all, and never present on a `csv_upload` source. The kind fixes the provider for the
    API kinds; a CSV source names its own. The clear key is never stored and never returned.
    `last_error` is a status line, never a provider's response body.
    """

    __tablename__ = "billing_sources"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=new_id)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    kind: Mapped[BillingSourceKind] = mapped_column(Text)
    provider: Mapped[BillingProvider] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    key_id: Mapped[str | None] = mapped_column(Text)
    # Anthropic `workspace_id` or OpenAI `project_id` to filter the provider's report by.
    provider_scope: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    status: Mapped[SourceStatus] = mapped_column(Text, server_default=text("'never_fetched'"))
    last_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self) -> str:
        # The default repr would include the sealed bytes; ids are all a log line needs.
        return f"BillingSource(id={self.id!s}, org_id={self.org_id!s})"


class BillingSourceProject(Base):
    """A source mapped to one project it covers.

    `org_id` and `provider` repeat the source's values so the composite foreign keys of
    migration 0601 can hold: a mapping to another organization's project, or with a provider
    other than the source's, cannot be written. One source per provider per project.
    """

    __tablename__ = "billing_source_projects"
    __table_args__ = (
        ForeignKeyConstraint(
            ["source_id", "org_id"],
            ["billing_sources.id", "billing_sources.org_id"],
            ondelete="CASCADE",
            name="billing_source_projects_source_id_org_id_fkey",
        ),
        ForeignKeyConstraint(
            ["source_id", "provider"],
            ["billing_sources.id", "billing_sources.provider"],
            ondelete="CASCADE",
            name="billing_source_projects_source_id_provider_fkey",
        ),
        ForeignKeyConstraint(
            ["project_id", "org_id"],
            ["projects.id", "projects.org_id"],
            ondelete="CASCADE",
            name="billing_source_projects_project_id_org_id_fkey",
        ),
    )

    source_id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True)
    org_id: Mapped[uuid.UUID] = mapped_column(UUID)
    provider: Mapped[BillingProvider] = mapped_column(Text)


class BillingFetchRun(Base):
    """One fetch from the provider, or one CSV upload, over a window of UTC days."""

    __tablename__ = "billing_fetch_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["source_id", "org_id"],
            ["billing_sources.id", "billing_sources.org_id"],
            ondelete="CASCADE",
            name="billing_fetch_runs_source_id_org_id_fkey",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=new_id)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID)
    org_id: Mapped[uuid.UUID] = mapped_column(UUID)
    trigger: Mapped[FetchTrigger] = mapped_column(Text)
    # Inclusive UTC days.
    day_from: Mapped[date] = mapped_column(Date)
    day_to: Mapped[date] = mapped_column(Date)
    status: Mapped[RunStatus] = mapped_column(Text)
    # A status line or one of the fixed short reasons, at most 200 characters; never a body.
    error: Mapped[str | None] = mapped_column(Text)
    lines_upserted: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    pages: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProviderCostLine(Base):
    """One provider line for one UTC day, as the provider or the CSV reported it, after mapping.

    `line_key` is the SHA-256 of the line's identity (64 lower-case hex digits), computed by the
    mapper; `(source_id, day, line_key)` is the upsert target, so a replayed fetch updates the
    same row and a provider correction changes `amount_usd`, `quantity`, `fetch_run_id` and
    `last_seen_at`. `amount_usd` is negative only on a credit. `org_id` and `provider` repeat
    the source's values, held equal by the composite foreign keys.
    """

    __tablename__ = "provider_cost_lines"
    __table_args__ = (
        ForeignKeyConstraint(
            ["source_id", "org_id"],
            ["billing_sources.id", "billing_sources.org_id"],
            ondelete="CASCADE",
            name="provider_cost_lines_source_id_org_id_fkey",
        ),
        ForeignKeyConstraint(
            ["source_id", "provider"],
            ["billing_sources.id", "billing_sources.provider"],
            ondelete="CASCADE",
            name="provider_cost_lines_source_id_provider_fkey",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=new_id)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID)
    org_id: Mapped[uuid.UUID] = mapped_column(UUID)
    # The run that last wrote the row.
    fetch_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("billing_fetch_runs.id", ondelete="RESTRICT")
    )
    day: Mapped[date] = mapped_column(Date)
    provider: Mapped[BillingProvider] = mapped_column(Text)
    line_key: Mapped[str] = mapped_column(Text)
    line_kind: Mapped[LineKind] = mapped_column(Text)
    # The provider's own model string, and the seed/override pattern it matched, if any.
    model_raw: Mapped[str | None] = mapped_column(Text)
    model_pattern: Mapped[str | None] = mapped_column(Text)
    token_type: Mapped[TokenType | None] = mapped_column(Text)
    service_tier: Mapped[ServiceTier | None] = mapped_column(Text)
    # `0-200k` / `200k-1M`, and the workspace or project id, as reported.
    context_window: Mapped[str | None] = mapped_column(Text)
    provider_scope: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text)
    # Tokens or requests when the report says (usage report, CSV); NULL otherwise, never 0.
    quantity: Mapped[int | None] = mapped_column(BigInteger)
    amount_usd: Mapped[Decimal] = mapped_column(Numeric(14, 8))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
