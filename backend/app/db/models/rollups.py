"""Rollups of spans and traces, rebuilt from the raw rows by the rollup jobs."""

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Integer, Numeric, Text, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base, _pg_enum
from app.db.models.telemetry import SpanKind


class SpanRollupHourly(Base):
    """Span metrics for one project, hour and (environment, provider, model, kind) group.

    Unknown dimensions are NULL. `cost_usd` is NULL when every span in the row is unpriced.
    The histograms hold 32 buckets (see `app.rollups.buckets`). The migration has the
    unique constraint over the dimensions.
    """

    __tablename__ = "span_rollups_hourly"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    bucket_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    environment: Mapped[str | None] = mapped_column(Text)
    provider: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[SpanKind] = mapped_column(_pg_enum(SpanKind, "span_kind"))
    span_count: Mapped[int] = mapped_column(BigInteger)
    errors: Mapped[int] = mapped_column(BigInteger)
    input_tokens: Mapped[int] = mapped_column(BigInteger)
    output_tokens: Mapped[int] = mapped_column(BigInteger)
    cached_tokens: Mapped[int] = mapped_column(BigInteger)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric)
    unpriced_calls: Mapped[int] = mapped_column(BigInteger)
    latency_buckets: Mapped[list[int]] = mapped_column(ARRAY(Integer))
    ttft_buckets: Mapped[list[int]] = mapped_column(ARRAY(Integer))
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class TraceRollupHourly(Base):
    """Trace counts for one project, hour and environment (NULL when unknown)."""

    __tablename__ = "trace_rollups_hourly"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    bucket_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    environment: Mapped[str | None] = mapped_column(Text)
    traces: Mapped[int] = mapped_column(BigInteger)
    errored_traces: Mapped[int] = mapped_column(BigInteger)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class CostRollupDaily(Base):
    """LLM span cost for one project, UTC day and (provider, model, service tier) group.

    Built by the daily cost rollup job; the reconciliation report reads it, never spans. Unknown
    dimensions are NULL, and the migration's unique expression index treats a NULL dimension as
    the empty string. `provider_canonical` and `model_pattern` are what the price table matched,
    NULL when nothing did. `cost_usd` includes `fee_usd`; both are NULL when no call of the row
    was priced. Row-level security applies.
    """

    __tablename__ = "cost_rollups_daily"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    day: Mapped[date] = mapped_column(Date)
    provider: Mapped[str | None] = mapped_column(Text)
    provider_canonical: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(Text)
    model_pattern: Mapped[str | None] = mapped_column(Text)
    service_tier: Mapped[str | None] = mapped_column(Text)
    calls: Mapped[int] = mapped_column(BigInteger)
    priced_calls: Mapped[int] = mapped_column(BigInteger)
    unpriced_calls: Mapped[int] = mapped_column(BigInteger)
    usage_unreported_calls: Mapped[int] = mapped_column(BigInteger)
    cut_stream_calls: Mapped[int] = mapped_column(BigInteger)
    cache_hit_calls: Mapped[int] = mapped_column(BigInteger)
    unmodelled_modifier_calls: Mapped[int] = mapped_column(BigInteger)
    uncached_input_tokens: Mapped[int] = mapped_column(BigInteger)
    cache_read_tokens: Mapped[int] = mapped_column(BigInteger)
    cache_write_5m_tokens: Mapped[int] = mapped_column(BigInteger)
    cache_write_1h_tokens: Mapped[int] = mapped_column(BigInteger)
    output_tokens: Mapped[int] = mapped_column(BigInteger)
    web_search_requests: Mapped[int] = mapped_column(BigInteger)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(14, 8))
    fee_usd: Mapped[Decimal | None] = mapped_column(Numeric(14, 8))
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ErrorRollupHourly(Base):
    """Failed `llm` spans for one project, hour and (environment, provider, model, error class,
    rate-limit kind) group.

    Unknown dimensions are NULL; the migration's unique key is `NULLS NOT DISTINCT`, so NULLs
    collide. `with_retry_after` counts the failed spans that carried a provider `retry-after`, a
    subset of `errors`. Fault-injected spans are not counted. Row-level security applies.
    """

    __tablename__ = "error_rollups_hourly"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    bucket_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    environment: Mapped[str | None] = mapped_column(Text)
    provider: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(Text)
    error_class: Mapped[str] = mapped_column(Text)
    rate_limit_kind: Mapped[str | None] = mapped_column(Text)
    errors: Mapped[int] = mapped_column(BigInteger)
    with_retry_after: Mapped[int] = mapped_column(BigInteger)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
