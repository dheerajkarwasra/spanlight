"""What the dataset service raises. The API maps each to a problem response
(`api/v1/datasets.py`)."""


class DatasetNotFoundError(Exception):
    """No dataset with this id in the project."""


class DatasetItemNotFoundError(Exception):
    """No item with this id in the dataset."""


class DatasetNameTakenError(Exception):
    """Another dataset of the project has this name."""


class DatasetInUseError(Exception):
    """An experiment uses the dataset, so it cannot be deleted."""


class DatasetFullError(Exception):
    """Adding the items would take the dataset past its limit."""

    def __init__(self, limit: int) -> None:
        super().__init__(f"a dataset can have at most {limit} items")
        self.limit = limit


class UnsupportedFileError(Exception):
    """The upload is not a `.jsonl` or `.csv` file of UTF-8 text."""
