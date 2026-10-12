"""The evaluator protocol."""

from __future__ import annotations

from typing import Protocol

from app.evals.schemas import EvalInput, EvalResult


class EvaluatorError(Exception):
    """An evaluator could not finish. `code` is a stable, secret-free reason."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class Evaluator(Protocol):
    """Scores one trace or span. Implementations must not log payloads."""

    name: str

    async def evaluate(self, input: EvalInput) -> EvalResult: ...
