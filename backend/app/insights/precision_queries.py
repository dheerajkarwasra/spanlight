"""Count right and wrong votes per detector kind, for the Doctor's precision table.

Every kind the project has an insight of is listed, with zero counts when nobody voted on it (its
ratios are then unknown, not 0). The window applies to the votes' `updated_at`: a vote counts in
the window it was last cast in. `precision_counts` is shared with the CLI's cross-project read.
"""

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import ColumnElement, Row, Select, and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Insight, InsightFeedback
from app.insights.precision import PrecisionRow


@dataclass(frozen=True, slots=True)
class FeedbackWindow:
    """`[start, end)` on `updated_at`; an open end is unbounded (all time by default)."""

    start: datetime | None = None
    end: datetime | None = None


ALL_TIME = FeedbackWindow()


def precision_counts(
    *where: ColumnElement[bool], window: FeedbackWindow = ALL_TIME
) -> Select[str, int, int]:
    """Kind, right votes and wrong votes, over the insights `where` selects, by kind."""
    joined: list[ColumnElement[bool]] = [
        InsightFeedback.project_id == Insight.project_id,
        InsightFeedback.insight_id == Insight.id,
    ]
    if window.start is not None:
        joined.append(InsightFeedback.updated_at >= window.start)
    if window.end is not None:
        joined.append(InsightFeedback.updated_at < window.end)
    return (
        select(
            Insight.kind,
            func.count(InsightFeedback.id).filter(InsightFeedback.verdict == "right"),
            func.count(InsightFeedback.id).filter(InsightFeedback.verdict == "wrong"),
        )
        .outerjoin(InsightFeedback, and_(*joined))
        .where(*where)
        .group_by(Insight.kind)
        .order_by(Insight.kind)
    )


def precision_rows_of(rows: Iterable[Row[str, int, int]]) -> list[PrecisionRow]:
    return [PrecisionRow(kind=kind, right=right, wrong=wrong) for kind, right, wrong in rows]


async def precision_rows(
    db: AsyncSession, project_id: uuid.UUID, window: FeedbackWindow
) -> list[PrecisionRow]:
    """One row per detector kind with an insight in the project, ordered by kind."""
    rows = await db.execute(precision_counts(Insight.project_id == project_id, window=window))
    return precision_rows_of(rows.all())
