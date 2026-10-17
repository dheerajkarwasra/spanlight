"""The Doctor's feedback: "Was this right?" on an insight, and the precision it measures.

A member and up votes on any insight of the project, whatever its status (`insights:feedback`;
one vote per member per insight, replaced by the next). Anyone who can read the project reads
the precision per detector kind (`project:read`). Responses are built before the commit, which
ends the project binding that row-level security needs.

Mounted before `insights`: otherwise `/insights/precision` would be read as an insight id.
"""

import uuid
from datetime import UTC, datetime
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Query, status

from app.api.deps import Access, DbSession, require
from app.api.schemas.insight_feedback import (
    FeedbackIn,
    FeedbackOut,
    PrecisionOut,
    feedback_out,
    precision_out,
)
from app.core.errors import FieldError, ProblemError, not_found
from app.core.observability import feature_used
from app.core.permissions import Permission
from app.insights import feedback_service
from app.insights.precision_queries import FeedbackWindow, precision_rows

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/projects/{project_id}/insights", tags=["insights"])

InsightReader = Annotated[Access, Depends(require(Permission.PROJECT_READ))]
FeedbackGiver = Annotated[Access, Depends(require(Permission.INSIGHTS_FEEDBACK))]


def feedback_window(
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: Annotated[datetime | None, Query()] = None,
) -> FeedbackWindow:
    """`[from, to)` on the votes' `updated_at`; either end may be left open (UTC if bare)."""
    start = None if from_ is None else _as_utc(from_)
    end = None if to is None else _as_utc(to)
    if start is not None and end is not None and start >= end:
        raise ProblemError(
            422,
            "VALIDATION_ERROR",
            "`from` must be earlier than `to`.",
            errors=[FieldError(field="from", message="must be earlier than `to`")],
        )
    return FeedbackWindow(start=start, end=end)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


@router.get("/precision", response_model=PrecisionOut, summary="Doctor precision per detector")
async def get_precision(
    project_id: uuid.UUID,
    access: InsightReader,
    db: DbSession,
    window: Annotated[FeedbackWindow, Depends(feedback_window)],
) -> PrecisionOut:
    """Right and wrong votes per detector kind, with every kind the project has an insight of.

    `precision` is right / n and `wilson_low`/`wilson_high` its Wilson 95 % interval, decimal
    strings to four places, `null` when the kind has no votes. `meets_target` is precision
    >= 0.8 with `wilson_low` >= 0.6 on n >= 20. `from`/`to` filter on when a vote was last cast
    (default: all time).
    """
    rows = await precision_rows(db, access.require_project().id, window)
    feature_used("insight_precision")
    return precision_out(rows)


@router.put(
    "/{insight_id}/feedback", response_model=FeedbackOut, summary="Say whether an insight was right"
)
async def put_feedback(
    project_id: uuid.UUID,
    insight_id: uuid.UUID,
    body: FeedbackIn,
    access: FeedbackGiver,
    db: DbSession,
) -> FeedbackOut:
    """Record your verdict (`right` or `wrong`) with an optional note of at most 500 characters,
    replacing your earlier vote. Allowed on a resolved or muted insight. `404` when the insight
    is not in the project."""
    project_id = access.require_project().id
    row = await feedback_service.put_feedback(
        db, project_id, insight_id, access.user_id, body.verdict, body.note
    )
    out = feedback_out(row)
    await db.commit()
    _counted(access, insight_id, "insight_feedback_put")
    return out


@router.delete(
    "/{insight_id}/feedback",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Withdraw your verdict on an insight",
)
async def delete_feedback(
    project_id: uuid.UUID, insight_id: uuid.UUID, access: FeedbackGiver, db: DbSession
) -> None:
    """`404` when you have no vote on the insight."""
    project_id = access.require_project().id
    if not await feedback_service.delete_feedback(db, project_id, insight_id, access.user_id):
        raise not_found()
    await db.commit()
    _counted(access, insight_id, "insight_feedback_delete")


def _counted(access: Access, insight_id: uuid.UUID, event: str) -> None:
    feature_used("insight_feedback")
    logger.info(
        event,
        org_id=str(access.org.id),
        project_id=str(access.require_project().id),
        insight_id=str(insight_id),
    )
