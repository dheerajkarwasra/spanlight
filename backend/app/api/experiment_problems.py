"""The problem responses for the errors the experiment service raises (`api/v1/experiments.py`)."""

from app.core.errors import FieldError, ProblemError, conflict, not_found
from app.evals.experiment_errors import (
    ExperimentConfigError,
    ExperimentNameTakenError,
    ExperimentNotFoundError,
    ExperimentReferenceNotFoundError,
    RunInProgressError,
    RunNotFinishedError,
)

EXPERIMENT_ERRORS = (
    ExperimentNotFoundError,
    ExperimentReferenceNotFoundError,
    ExperimentNameTakenError,
    ExperimentConfigError,
    RunInProgressError,
    RunNotFinishedError,
)


def experiment_problem(error: Exception) -> ProblemError:
    """The problem response for an error the experiment service raises."""
    if isinstance(error, ExperimentNotFoundError | ExperimentReferenceNotFoundError):
        return not_found()
    if isinstance(error, ExperimentNameTakenError):
        return conflict("NAME_TAKEN", "An experiment with this name already exists here.")
    if isinstance(error, RunInProgressError):
        return conflict(
            "RUN_IN_PROGRESS", "This experiment already has a run in progress; wait for it to end."
        )
    if isinstance(error, RunNotFinishedError):
        return conflict(
            "RUN_IN_PROGRESS", "A run is still queued or running; compare it once it ends."
        )
    if isinstance(error, ExperimentConfigError):
        return ProblemError(
            422,
            error.code,
            error.detail,
            errors=[FieldError(field=error.field, message=error.detail)],
        )
    raise error
