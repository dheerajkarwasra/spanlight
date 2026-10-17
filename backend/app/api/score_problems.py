"""Problem responses for the errors the score config service raises and for a score the write
path rejects, shared by the score routes (`api/v1/scores.py`)."""

from pydantic import ValidationError

from app.core.errors import FieldError, ProblemError, conflict, not_found
from app.scores.errors import (
    ScoreConfigInUseError,
    ScoreConfigLimitError,
    ScoreConfigNameTakenError,
    ScoreConfigNotFoundError,
)
from app.scores.service import TOO_MANY_NAMES

CONFIG_ERRORS = (
    ScoreConfigLimitError,
    ScoreConfigNotFoundError,
    ScoreConfigNameTakenError,
    ScoreConfigInUseError,
    ValidationError,
)


def config_problem(error: Exception) -> ProblemError:
    """The problem response for an error the score config service raises."""
    if isinstance(error, ScoreConfigNotFoundError):
        return not_found()
    if isinstance(error, ScoreConfigNameTakenError):
        return conflict("NAME_TAKEN", "A score config with this name already exists here.")
    if isinstance(error, ScoreConfigInUseError):
        return conflict(
            "SCORE_CONFIG_IN_USE",
            "Scores use this name, so the config cannot be deleted or change type.",
        )
    if isinstance(error, ScoreConfigLimitError):
        return score_rejection(TOO_MANY_NAMES)
    if isinstance(error, ValidationError):
        fields = [
            FieldError(
                field=".".join(str(part) for part in item["loc"]) or "body", message=item["msg"]
            )
            for item in error.errors()
        ]
        return ProblemError(422, "VALIDATION_ERROR", "The request is invalid.", errors=fields)
    raise error


def score_rejection(reason: str) -> ProblemError:
    """A score the write path rejected, as a field error (`<field>: <message>`)."""
    field, _, message = reason.partition(": ")
    if not message:
        field, message = "body", reason
    return ProblemError(
        422,
        "VALIDATION_ERROR",
        "The score does not fit its config.",
        errors=[FieldError(field=field, message=message)],
    )
