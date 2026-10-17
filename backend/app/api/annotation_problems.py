"""Problem responses for the errors the annotations service raises, shared by the queue routes
(`api/v1/annotations.py`)."""

from app.annotations.errors import (
    ItemNotAssignedError,
    ItemNotPendingError,
    QueueInUseError,
    QueueItemNotFoundError,
    QueueNameTakenError,
    QueueNotFoundError,
    ScoreNotInQueueError,
    ScoreRejectedError,
    ScoreRequiredError,
    UnknownScoreConfigError,
    UnknownTraceError,
)
from app.core.errors import FieldError, ProblemError, conflict, not_found

QUEUE_ERRORS = (
    QueueNotFoundError,
    QueueItemNotFoundError,
    QueueNameTakenError,
    QueueInUseError,
    UnknownScoreConfigError,
    UnknownTraceError,
    ItemNotAssignedError,
    ItemNotPendingError,
    ScoreNotInQueueError,
    ScoreRequiredError,
    ScoreRejectedError,
)


def _scores_problem(code: str, detail: str, names: list[str]) -> ProblemError:
    errors = [FieldError(field=f"scores.{name}", message=detail) for name in names]
    return ProblemError(422, code, detail, errors=errors)


def queue_problem(error: Exception) -> ProblemError:
    """The problem response for an error the annotations service raises."""
    if isinstance(error, QueueNotFoundError | QueueItemNotFoundError):
        return not_found()
    if isinstance(error, QueueNameTakenError):
        return conflict("NAME_TAKEN", "An annotation queue with this name already exists here.")
    if isinstance(error, QueueInUseError):
        return conflict(
            "QUEUE_IN_USE",
            "A review policy fills this queue. Delete the policy or point it at another queue "
            "first.",
        )
    if isinstance(error, UnknownScoreConfigError):
        return ProblemError(
            422, "UNKNOWN_SCORE_CONFIG", "A score config of the queue is not in this project."
        )
    if isinstance(error, UnknownTraceError):
        fields = [
            FieldError(field="trace_ids", message=f"unknown trace {t}") for t in error.trace_ids
        ]
        return ProblemError(
            422, "VALIDATION_ERROR", "Some traces are not in this project.", errors=fields
        )
    if isinstance(error, ItemNotAssignedError):
        return conflict("ITEM_NOT_ASSIGNED_TO_YOU", "This item is not claimed by you.")
    if isinstance(error, ItemNotPendingError):
        return conflict("ITEM_NOT_PENDING", "This item is already done or skipped.")
    if isinstance(error, ScoreNotInQueueError):
        return _scores_problem(
            "SCORE_NOT_IN_QUEUE", "This score is not one of the queue's configs.", error.names
        )
    if isinstance(error, ScoreRequiredError):
        return _scores_problem("SCORE_REQUIRED", "This score is required.", error.names)
    if isinstance(error, ScoreRejectedError):
        field, _, message = error.reason.partition(": ")
        if not message:
            field, message = "body", error.reason
        return ProblemError(
            422,
            "VALIDATION_ERROR",
            "The score does not fit its config.",
            errors=[FieldError(field=field, message=message)],
        )
    raise error
