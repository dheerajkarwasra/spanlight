"""Scores on traces and the configs that say what each score name means."""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, Text, func, text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.ids import new_id
from app.db.models.base import Base, _pg_enum
from app.scores.types import ScoreDataType, ScoreSource


class ScoreConfig(Base):
    """What a score name means in a project. Row-level security applies.

    A numeric config may bound its values (`min_value`, `max_value`; NULL is open); a categorical
    one may list its categories (empty is an open list); a boolean one has neither. `implicit`
    configs were created by the first score with that name; one made or edited in the dashboard
    is explicit.
    """

    __tablename__ = "score_configs"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=new_id)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(Text)
    data_type: Mapped[ScoreDataType] = mapped_column(_pg_enum(ScoreDataType, "score_data_type"))
    min_value: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    max_value: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    categories: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    description: Mapped[str | None] = mapped_column(Text)
    implicit: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Score(Base):
    """One named judgement about a trace or a span. Row-level security applies.

    The key is `(project_id, id)`: clients choose ids for idempotent retries, and a global key
    would let another tenant's id silently swallow a score. `trace_id` and `span_id` are not
    foreign keys, since a score may arrive before its trace. `value` holds numeric and boolean
    (0/1) scores, `string_value` categorical ones.
    """

    __tablename__ = "scores"

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=new_id)
    trace_id: Mapped[str] = mapped_column(Text)
    span_id: Mapped[str | None] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    data_type: Mapped[ScoreDataType] = mapped_column(_pg_enum(ScoreDataType, "score_data_type"))
    value: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    string_value: Mapped[str | None] = mapped_column(Text)
    source: Mapped[ScoreSource] = mapped_column(_pg_enum(ScoreSource, "score_source"))
    comment: Mapped[str | None] = mapped_column(Text)
    author_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    evaluator_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("evaluator_runs.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
