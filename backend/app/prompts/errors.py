"""What the prompt service raises. The API maps each to a problem response
(`api/v1/prompts.py`)."""


class PromptNotFoundError(Exception):
    """No prompt, version or label with this name in the project."""


class PromptNameTakenError(Exception):
    """Another prompt of the project has this name."""


class KindMismatchError(Exception):
    """A new version has another kind than the prompt (a prompt keeps its kind)."""


class NoChangesError(Exception):
    """The new version's template and model config equal the latest version's."""


class PromptInUseError(Exception):
    """An evaluator (or, later, an experiment) uses one of the prompt's versions."""


class LabelReservedError(Exception):
    """`latest` always means the highest version and cannot be set as a label."""


class VersionRateLimitedError(Exception):
    """The prompt already got its daily limit of versions."""

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("too many versions today")
        self.retry_after_seconds = retry_after_seconds
