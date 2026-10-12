"""What the experiment service raises. The API maps each to a problem response
(`app/api/experiment_problems.py`)."""


class ExperimentNotFoundError(Exception):
    """No experiment (or run of it) with this id in the project."""


class ExperimentReferenceNotFoundError(Exception):
    """The dataset, an evaluator, the prompt version or the credential is not the project's or
    the organization's."""


class ExperimentNameTakenError(Exception):
    """Another experiment of the project has this name."""


class RunInProgressError(Exception):
    """The experiment already has a queued or running run."""


class RunNotFinishedError(Exception):
    """A run is still queued or running, so it cannot be compared yet."""


class ExperimentConfigError(Exception):
    """The config is well formed but not allowed. `code` is the stable problem code, `field` the
    body member at fault."""

    def __init__(self, code: str, detail: str, field: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.field = field
