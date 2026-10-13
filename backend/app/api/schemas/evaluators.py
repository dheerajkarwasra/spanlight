"""Evaluators, their runs, a judge's budget and a test run.

The bodies that create and edit evaluators are owned by the evals domain (`app.evals.config`),
which validates them; they are re-exported here. Money is a decimal string. A budget member is
`null` when the evaluator has no budget, never zero.
"""

import uuid
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from app.api.schemas.common import ApiModel, Money
from app.api.schemas.scores import ScoreNumber
from app.db.models import Evaluator, EvaluatorRun
from app.evals.config import (
    EvaluatorConfig,
    EvaluatorFilters,
    EvaluatorPatch,
    EvaluatorSpec,
)
from app.evals.schemas import EvalResult
from app.evals.service import BudgetStatus, BudgetSummary
from app.ingest.schemas import TraceId
from app.scores.queries import ScoreSummary

__all__ = [
    "EvalResultOut",
    "EvalScoreOut",
    "EvaluatorBudgetOut",
    "EvaluatorBudgetStatusOut",
    "EvaluatorOut",
    "EvaluatorPatch",
    "EvaluatorRunOut",
    "EvaluatorSpec",
    "EvaluatorTestIn",
    "RunScoreOut",
]


class EvaluatorBudgetOut(ApiModel):
    """A judge's budget and its UTC calendar month so far; `reserved_usd` is held by running
    calls."""

    monthly_budget_usd: Money
    spent_usd: Money
    reserved_usd: Money

    @classmethod
    def of(cls, summary: BudgetSummary) -> "EvaluatorBudgetOut":
        return cls(
            monthly_budget_usd=summary.monthly_budget_usd,
            spent_usd=summary.spent_usd,
            reserved_usd=summary.reserved_usd,
        )


class EvaluatorOut(ApiModel):
    """An evaluator. `budget` is `null` for an evaluator without a monthly budget."""

    id: uuid.UUID
    name: str
    kind: str
    config: EvaluatorConfig
    sampling_rate: Decimal
    filters: EvaluatorFilters
    monthly_budget_usd: Money | None
    enabled: bool
    created_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    budget: EvaluatorBudgetOut | None

    @classmethod
    def of(cls, evaluator: Evaluator, budget: BudgetSummary | None) -> "EvaluatorOut":
        return cls.model_validate(
            {
                "id": evaluator.id,
                "name": evaluator.name,
                "kind": evaluator.kind,
                "config": evaluator.config,
                "sampling_rate": evaluator.sampling_rate,
                "filters": evaluator.filters,
                "monthly_budget_usd": evaluator.monthly_budget_usd,
                "enabled": evaluator.enabled,
                "created_by": evaluator.created_by,
                "created_at": evaluator.created_at,
                "updated_at": evaluator.updated_at,
                "budget": None if budget is None else EvaluatorBudgetOut.of(budget),
            }
        )


class RunScoreOut(ApiModel):
    """A score a run wrote: `value` for numeric and boolean (1/0) scores, `string_value` for
    categorical ones."""

    name: str
    value: ScoreNumber | None
    string_value: str | None


class EvaluatorRunOut(ApiModel):
    """One evaluation. `source` is why it ran (`online`, `test` or `experiment`); `cost_usd` is
    what the run is charged (zero until it settles); `reserved_usd` what a running judge call
    holds against the budget; `scores` the scores it wrote (empty until it is done)."""

    id: uuid.UUID
    trace_id: str
    span_id: str | None
    status: str
    source: str
    cost_usd: Money
    reserved_usd: Money
    error: str | None
    started_at: datetime
    heartbeat_at: datetime | None
    finished_at: datetime | None
    scores: list[RunScoreOut]

    @classmethod
    def of(cls, run: EvaluatorRun, scores: Sequence[ScoreSummary]) -> "EvaluatorRunOut":
        return cls(
            id=run.id,
            trace_id=run.trace_id,
            span_id=run.span_id,
            status=run.status,
            source=run.source,
            cost_usd=run.cost_usd,
            reserved_usd=run.reserved_usd,
            error=run.error,
            started_at=run.started_at,
            heartbeat_at=run.heartbeat_at,
            finished_at=run.finished_at,
            scores=[
                RunScoreOut(name=s.name, value=s.value, string_value=s.string_value) for s in scores
            ],
        )


class EvaluatorBudgetStatusOut(ApiModel):
    """The month so far (UTC calendar month). `remaining_usd` counts reservations as spent."""

    monthly_budget_usd: Money | None
    spent_usd: Money
    reserved_usd: Money
    remaining_usd: Money | None
    month_start: datetime

    @classmethod
    def of(cls, status: BudgetStatus) -> "EvaluatorBudgetStatusOut":
        return cls(
            monthly_budget_usd=status.monthly_budget_usd,
            spent_usd=status.spent_usd,
            reserved_usd=status.reserved_usd,
            remaining_usd=status.remaining_usd,
            month_start=status.month_start,
        )


class EvaluatorTestIn(ApiModel):
    trace_id: TraceId


class EvalScoreOut(ApiModel):
    name: str
    span_id: str | None
    value: ScoreNumber | None
    string_value: str | None
    comment: str | None


class EvalResultOut(ApiModel):
    """What the evaluator would score; nothing is stored. `cost_usd` is the call's real cost."""

    scores: list[EvalScoreOut]
    cost_usd: Money
    reasoning: str | None

    @classmethod
    def of(cls, result: EvalResult) -> "EvalResultOut":
        scores = [
            EvalScoreOut(
                name=score.name,
                span_id=score.span_id,
                # A boolean score is stored as 1 or 0.
                value=Decimal(int(score.value)) if isinstance(score.value, bool) else score.value,
                string_value=score.string_value,
                comment=score.comment,
            )
            for score in result.scores
        ]
        return cls(scores=scores, cost_usd=result.cost_usd, reasoning=result.reasoning)
