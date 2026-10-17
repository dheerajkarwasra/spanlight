"""Record and withdraw a member's "Was this right?" vote on an insight.

Any insight of the project can be voted on, whatever its status: a resolved or muted finding was
still right or wrong, and precision counts it. The caller's transaction is bound to the project.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import not_found
from app.db.models import Insight, InsightFeedback
from app.insights.feedback_queries import Verdict, delete_vote, upsert_vote


async def put_feedback(
    db: AsyncSession,
    project_id: uuid.UUID,
    insight_id: uuid.UUID,
    user_id: uuid.UUID,
    verdict: Verdict,
    note: str | None,
) -> InsightFeedback:
    """Record the caller's vote, replacing an earlier one. `404` when the insight is not in the
    project. An empty note is stored as no note."""
    await _lock_insight(db, project_id, insight_id)
    return await upsert_vote(db, project_id, insight_id, user_id, verdict, note or None)


async def delete_feedback(
    db: AsyncSession, project_id: uuid.UUID, insight_id: uuid.UUID, user_id: uuid.UUID
) -> bool:
    """Withdraw the caller's vote; whether there was one to withdraw."""
    return await delete_vote(db, project_id, insight_id, user_id)


async def _lock_insight(db: AsyncSession, project_id: uuid.UUID, insight_id: uuid.UUID) -> None:
    """Find the insight and hold it (`FOR KEY SHARE`, what the foreign key check takes anyway)
    until the vote is written, so a concurrent delete waits instead of failing the insert."""
    found = await db.scalar(
        select(Insight.id)
        .where(Insight.project_id == project_id, Insight.id == insight_id)
        .with_for_update(read=True, key_share=True)
    )
    if found is None:
        raise not_found()
