"""Scores on traces and the score configs of a project.

The config bodies, `ScoreConfigSpec` and `ScoreConfigPatch`, are owned by the scores domain
(`app.scores.schemas`), which validates them; they are re-exported here so routers keep
importing from `app.api.schemas`. Score values and config bounds are JSON numbers with at most
four decimals; a boolean score's value is 1 or 0.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Self

from pydantic import ConfigDict, PlainSerializer

from app.api.schemas.common import ApiModel
from app.db.models import Score
from app.scores.schemas import (
    Comment,
    ScoreConfigPatch,
    ScoreConfigSpec,
    ScoreName,
    ScoreValue,
    SpanRef,
    StringValue,
)
from app.scores.types import ScoreDataType, ScoreSource

__all__ = [
    "ScoreConfigOut",
    "ScoreConfigPatch",
    "ScoreConfigSpec",
    "ScoreCreate",
    "ScoreOut",
    "ScoreSummaryOut",
]

# Four decimals within ±1e9 fit a double exactly enough to round-trip.
ScoreNumber = Annotated[Decimal, PlainSerializer(float, return_type=float, when_used="json")]


class ScoreConfigOut(ApiModel):
    id: uuid.UUID
    name: str
    data_type: ScoreDataType
    # Numeric only; null is an open end.
    min_value: ScoreNumber | None
    max_value: ScoreNumber | None
    # Categorical only; empty is an open list.
    categories: list[str]
    description: str | None
    # True while the config is the one the first score with this name created.
    implicit: bool
    created_at: datetime
    updated_at: datetime


class ScoreSummaryOut(ApiModel):
    """A trace's latest score of one name, as the trace list shows it."""

    name: str
    value: ScoreNumber | None
    string_value: str | None


class ScoreCreate(ApiModel):
    """A score a person adds to a trace on the dashboard. Send exactly one of `value` (a number,
    or `true`/`false`) and `string_value`. `id` makes a retry idempotent."""

    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID | None = None
    span_id: SpanRef | None = None
    name: ScoreName
    value: ScoreValue | None = None
    string_value: StringValue | None = None
    comment: Comment | None = None


class ScoreOut(ApiModel):
    id: uuid.UUID
    trace_id: str
    span_id: str | None
    name: str
    data_type: ScoreDataType
    # Numeric and boolean (1/0) scores.
    value: ScoreNumber | None
    # Categorical scores.
    string_value: str | None
    source: ScoreSource
    comment: str | None
    # The person who wrote a `human` score; null for other sources or a deleted user.
    author_user_id: uuid.UUID | None
    author_name: str | None
    evaluator_run_id: uuid.UUID | None
    created_at: datetime

    @classmethod
    def of(cls, score: Score, author_name: str | None) -> Self:
        return cls(
            id=score.id,
            trace_id=score.trace_id,
            span_id=score.span_id,
            name=score.name,
            data_type=score.data_type,
            value=score.value,
            string_value=score.string_value,
            source=score.source,
            comment=score.comment,
            author_user_id=score.author_user_id,
            author_name=author_name,
            evaluator_run_id=score.evaluator_run_id,
            created_at=score.created_at,
        )
