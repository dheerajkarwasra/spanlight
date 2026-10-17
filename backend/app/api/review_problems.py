"""Problem responses for the errors the review policy service raises
(`api/v1/review_policy.py`)."""

from app.core.errors import ProblemError, conflict, not_found
from app.review.calendar import MAX_MANUAL_DAYS_BACK
from app.review.errors import (
    AlreadySelectedError,
    DayNotCompleteError,
    DayTooOldError,
    NothingSelectedError,
    PolicyNotFoundError,
    QueueLacksFailureCategoryError,
    QueueNotInProjectError,
)

REVIEW_ERRORS = (
    PolicyNotFoundError,
    QueueNotInProjectError,
    QueueLacksFailureCategoryError,
    NothingSelectedError,
    DayNotCompleteError,
    DayTooOldError,
    AlreadySelectedError,
)


def review_problem(error: Exception) -> ProblemError:
    """The problem response for an error the review policy service raises."""
    if isinstance(error, PolicyNotFoundError | QueueNotInProjectError):
        return not_found()
    if isinstance(error, QueueLacksFailureCategoryError):
        return ProblemError(
            422,
            "QUEUE_LACKS_FAILURE_CATEGORY",
            "The queue must ask for the failure_category score, so every reviewed trace gets "
            "a category. Add it to the queue first.",
        )
    if isinstance(error, NothingSelectedError):
        return ProblemError(
            422,
            "NOTHING_SELECTED",
            "This policy would never queue a trace: turn on an include or set a random rate.",
        )
    if isinstance(error, DayNotCompleteError):
        return ProblemError(
            422, "DAY_NOT_COMPLETE", "Only a completed UTC day can be selected; today cannot."
        )
    if isinstance(error, DayTooOldError):
        return ProblemError(
            422,
            "DAY_TOO_OLD",
            f"A manual run covers at most {MAX_MANUAL_DAYS_BACK} days back.",
        )
    if isinstance(error, AlreadySelectedError):
        return conflict("ALREADY_SELECTED", "This day has already been selected.")
    raise error
