"""The reads and writes behind "Was this right?": one vote per member per insight.

`insight_feedback` is under row-level security, so the caller's transaction is bound to the
project; the explicit `project_id` filters are a second guard.
"""

import uuid
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import InsightFeedback

Verdict = Literal["right", "wrong"]


@dataclass(frozen=True, slots=True)
class FeedbackSummary:
    """The caller's own vote (None when they have not voted) and the counts of every vote."""

    mine: InsightFeedback | None
    right: int
    wrong: int


async def upsert_vote(
    db: AsyncSession,
    project_id: uuid.UUID,
    insight_id: uuid.UUID,
    user_id: uuid.UUID,
    verdict: Verdict,
    note: str | None,
) -> InsightFeedback:
    """Insert the vote, or replace the caller's earlier one (`insight_feedback_vote_key`)."""
    statement = (
        insert(InsightFeedback)
        .values(
            project_id=project_id,
            insight_id=insight_id,
            user_id=user_id,
            verdict=verdict,
            note=note,
        )
        .on_conflict_do_update(
            constraint="insight_feedback_vote_key",
            set_={"verdict": verdict, "note": note, "updated_at": func.now()},
        )
        .returning(InsightFeedback)
    )
    result = await db.scalars(statement, execution_options={"populate_existing": True})
    row: InsightFeedback = result.one()
    return row


async def delete_vote(
    db: AsyncSession, project_id: uuid.UUID, insight_id: uuid.UUID, user_id: uuid.UUID
) -> bool:
    """Remove the caller's vote; whether there was one."""
    result = await db.execute(
        delete(InsightFeedback)
        .where(
            InsightFeedback.project_id == project_id,
            InsightFeedback.insight_id == insight_id,
            InsightFeedback.user_id == user_id,
        )
        .returning(InsightFeedback.id)
    )
    return result.first() is not None


async def feedback_summary(
    db: AsyncSession, project_id: uuid.UUID, insight_id: uuid.UUID, user_id: uuid.UUID
) -> FeedbackSummary:
    """The caller's vote and the counts of right and wrong votes on one insight."""
    of_insight = (
        InsightFeedback.project_id == project_id,
        InsightFeedback.insight_id == insight_id,
    )
    counts = (
        await db.execute(
            select(
                func.count().filter(InsightFeedback.verdict == "right"),
                func.count().filter(InsightFeedback.verdict == "wrong"),
            ).where(*of_insight)
        )
    ).one()
    mine = await db.scalar(
        select(InsightFeedback).where(*of_insight, InsightFeedback.user_id == user_id)
    )
    return FeedbackSummary(mine=mine, right=counts[0], wrong=counts[1])
