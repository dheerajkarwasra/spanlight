"""End-user analytics: one row per project, UTC day and end user, refreshed from the raw rows."""

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base


class UserStatsDaily(Base):
    """What one end user (`traces.external_user_id`) did in one environment on one UTC day.
    Row-level security applies.

    The traces started that day, the LLM calls they made, how many failed, how many had no price,
    the tokens used (input plus output) and the cost of the priced calls. `cost_usd` is NULL when
    no call of the day was priced, never 0. The `refresh_user_stats` job (`app.end_users.jobs`) owns
    the rows; `computed_at` records when it wrote one. Unknown environments are NULL, and the
    table's key (migration 1015) is `UNIQUE NULLS NOT DISTINCT (project_id, day, environment,
    external_user_id)`, not a primary key; the mapper uses those columns as its identity.
    """

    __tablename__ = "user_stats_daily"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "day",
            "environment",
            "external_user_id",
            name="user_stats_daily_natural_key",
            postgresql_nulls_not_distinct=True,
        ),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    day: Mapped[date] = mapped_column(Date)
    environment: Mapped[str | None] = mapped_column(Text)
    external_user_id: Mapped[str] = mapped_column(Text)
    traces: Mapped[int] = mapped_column(Integer)
    llm_calls: Mapped[int] = mapped_column(Integer)
    errors: Mapped[int] = mapped_column(Integer)
    unpriced_calls: Mapped[int] = mapped_column(Integer)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(14, 8))
    tokens: Mapped[int] = mapped_column(BigInteger)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # SQLAlchemy reads this class attribute; mypy rejects a ClassVar annotation on it.
    __mapper_args__ = {  # noqa: RUF012
        "primary_key": [project_id, day, environment, external_user_id]
    }
