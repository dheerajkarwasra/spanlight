"""The versioned model price table."""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.ids import new_id
from app.db.models.base import Base


class ModelPrice(Base):
    __tablename__ = "model_prices"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=new_id)
    provider: Mapped[str] = mapped_column(Text)
    model_pattern: Mapped[str] = mapped_column(Text)
    input_per_mtok: Mapped[Decimal] = mapped_column(Numeric(12, 6))
    output_per_mtok: Mapped[Decimal] = mapped_column(Numeric(12, 6))
    cached_input_per_mtok: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    cache_write_5m_per_mtok: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    cache_write_1h_per_mtok: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    # The smallest prompt this row prices, 0 for the base price; part of the unique key.
    min_prompt_tokens: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    version: Mapped[str] = mapped_column(Text)


class PriceOverride(Base):
    """An organization's own rate for a model; wins over the seed row for the same pattern.

    Scoped to the organization and not under row-level security: every query filters by `org_id`.
    `provider` and `model_pattern` are stored lower-case, as in `model_prices`.
    """

    __tablename__ = "price_overrides"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=new_id)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(Text)
    model_pattern: Mapped[str] = mapped_column(Text)
    input_per_mtok: Mapped[Decimal] = mapped_column(Numeric(12, 6))
    output_per_mtok: Mapped[Decimal] = mapped_column(Numeric(12, 6))
    cached_input_per_mtok: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    cache_write_5m_per_mtok: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    cache_write_1h_per_mtok: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    # The smallest prompt this row prices, 0 for the base price; part of the unique key.
    min_prompt_tokens: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
