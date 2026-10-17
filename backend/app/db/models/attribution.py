"""The daily attribution rollup: cost and usage per group of traces, rebuilt by the refresh job."""

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Integer, Numeric, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base


class AttributionRollupDaily(Base):
    """One project's traces of one UTC day, environment and group. Row-level security applies.

    `group_kind` is `metadata` (with the promoted key in `group_key`), `trace_name`, `user` or
    `release`. `bucket` is `value` (one real value in `group_value`), `none` (the traces without
    the dimension) or `other` (the values folded past the cap, counted in `folded_values`).
    Unknown environments are NULL, and the migration's natural key is `UNIQUE NULLS NOT
    DISTINCT`, so the refresh upserts them too. `cost_usd` includes fees; it, `retries` and
    `retry_cost_usd` are NULL when nothing was priced or reported, never 0.
    """

    __tablename__ = "attribution_rollups_daily"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    day: Mapped[date] = mapped_column(Date)
    environment: Mapped[str | None] = mapped_column(Text)
    group_kind: Mapped[str] = mapped_column(Text)
    group_key: Mapped[str | None] = mapped_column(Text)
    bucket: Mapped[str] = mapped_column(Text)
    group_value: Mapped[str | None] = mapped_column(Text)
    traces: Mapped[int] = mapped_column(Integer)
    llm_calls: Mapped[int] = mapped_column(BigInteger)
    errors: Mapped[int] = mapped_column(BigInteger)
    unpriced_calls: Mapped[int] = mapped_column(BigInteger)
    input_tokens: Mapped[int] = mapped_column(BigInteger)
    output_tokens: Mapped[int] = mapped_column(BigInteger)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(14, 8))
    retries: Mapped[int | None] = mapped_column(BigInteger)
    retry_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(14, 8))
    folded_values: Mapped[int | None] = mapped_column(Integer)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
