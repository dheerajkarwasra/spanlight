"""The problem responses for the errors the dataset service raises (`api/v1/datasets.py`)."""

from app.core.errors import ProblemError, conflict, not_found
from app.datasets.errors import (
    DatasetFullError,
    DatasetInUseError,
    DatasetItemNotFoundError,
    DatasetNameTakenError,
    DatasetNotFoundError,
    UnsupportedFileError,
)

DATASET_ERRORS = (
    DatasetNotFoundError,
    DatasetItemNotFoundError,
    DatasetNameTakenError,
    DatasetInUseError,
    DatasetFullError,
    UnsupportedFileError,
)


def dataset_problem(error: Exception) -> ProblemError:
    """The problem response for an error the dataset service raises."""
    if isinstance(error, DatasetNotFoundError | DatasetItemNotFoundError):
        return not_found()
    if isinstance(error, DatasetNameTakenError):
        return conflict("NAME_TAKEN", "A dataset with this name already exists here.")
    if isinstance(error, DatasetInUseError):
        return conflict(
            "DATASET_IN_USE", "An experiment uses this dataset; it cannot be deleted while it does."
        )
    if isinstance(error, DatasetFullError):
        return ProblemError(422, "DATASET_FULL", f"A dataset can have at most {error.limit} items.")
    if isinstance(error, UnsupportedFileError):
        return ProblemError(415, "UNSUPPORTED_FILE", str(error))
    raise error
