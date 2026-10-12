"""Trace-list filters on scores.

A trace passes when its latest score of the named score is inside the asked range or equals the
asked category. "Latest" is the highest `created_at`, ties broken by the higher id.
"""

from decimal import Decimal

from sqlalchemy import exists, tuple_
from sqlalchemy.orm import aliased
from sqlalchemy.sql.elements import ColumnElement

from app.core.errors import ProblemError
from app.db.models import Score, Trace

SCORE_FILTER_NEEDS_NAME = "SCORE_FILTER_NEEDS_NAME"


def check_score_filters(
    name: str | None, minimum: Decimal | None, maximum: Decimal | None, value: str | None
) -> None:
    """Reject a range or category without the score name it applies to."""
    if name is None and (minimum is not None or maximum is not None or value is not None):
        raise ProblemError(
            422,
            SCORE_FILTER_NEEDS_NAME,
            "score_min, score_max and score_value need score_name.",
        )


def latest_score_matches(
    name: str, minimum: Decimal | None, maximum: Decimal | None, value: str | None
) -> ColumnElement[bool]:
    """The trace's latest score called `name` exists and satisfies the asked bounds."""
    latest = aliased(Score)
    newer = aliased(Score)
    conditions: list[ColumnElement[bool]] = [
        latest.project_id == Trace.project_id,
        latest.trace_id == Trace.trace_id,
        latest.name == name,
        ~exists().where(
            newer.project_id == latest.project_id,
            newer.trace_id == latest.trace_id,
            newer.name == latest.name,
            tuple_(newer.created_at, newer.id) > tuple_(latest.created_at, latest.id),
        ),
    ]
    if minimum is not None:
        conditions.append(latest.value >= minimum)
    if maximum is not None:
        conditions.append(latest.value <= maximum)
    if value is not None:
        conditions.append(latest.string_value == value)
    return exists().where(*conditions)


def score_conditions(
    name: str | None,
    minimum: Decimal | None,
    maximum: Decimal | None,
    value: str | None,
    has_scores: bool | None,
) -> list[ColumnElement[bool]]:
    """The trace-list conditions the score query parameters ask for."""
    conditions: list[ColumnElement[bool]] = []
    if name is not None:
        conditions.append(latest_score_matches(name, minimum, maximum, value))
    if has_scores is not None:
        # A bare `has_scores` (not `IS true`) lets the planner use the partial index
        # `traces_project_scored_started_idx ... WHERE has_scores`.
        conditions.append(Trace.has_scores.expression if has_scores else ~Trace.has_scores)
    return conditions
