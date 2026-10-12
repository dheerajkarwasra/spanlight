"""Evaluator inputs and results shared by every evaluator kind."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.scores.schemas import ScoreIn


@dataclass(frozen=True, slots=True)
class EvalInput:
    """What an evaluator sees: one trace or one span, already redacted at ingest."""

    trace_id: str
    span_id: str | None
    input: Any
    output: Any
    expected_output: Any | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EvalResult:
    """Scores an evaluator produced, what it cost, and an optional explanation."""

    scores: list[ScoreIn]
    cost_usd: Decimal = Decimal(0)
    reasoning: str | None = None
