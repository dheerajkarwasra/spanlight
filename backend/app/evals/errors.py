"""What the evaluator service raises. The API maps each to a problem response
(`api/v1/evaluators.py`)."""


class EvaluatorNotFoundError(Exception):
    """No evaluator with this id in the project."""


class EvaluatorNameTakenError(Exception):
    """Another evaluator of the project has this name."""


class EvaluatorReferenceNotFoundError(Exception):
    """The config names a prompt version or credential the project or organization lacks."""


class EvaluatorConfigError(Exception):
    """The config is well formed but not allowed. `code` is the stable problem code, `field` the
    body member at fault."""

    def __init__(self, code: str, detail: str, field: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.field = field
