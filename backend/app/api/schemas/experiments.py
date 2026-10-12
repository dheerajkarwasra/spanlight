"""Experiments, their runs, run totals and run items.

The body that creates an experiment is owned by the evals domain
(`app.evals.experiment_config`), which validates it; it is re-exported here. Money is a decimal
string; an unknown cost or latency is `null`, never zero.
"""

import uuid
from datetime import datetime
from typing import Any

from app.api.schemas.common import ApiModel, Money
from app.db.models import Experiment, ExperimentItem, ExperimentRun
from app.evals.experiment_config import (
    ExperimentConfig,
    ExperimentSpec,
    RunStart,
    Target,
    parse_target,
)
from app.evals.experiment_queries import RunSummary
from app.evals.experiment_totals import Totals, totals_from_json

__all__ = [
    "ExperimentItemOut",
    "ExperimentOut",
    "ExperimentRunOut",
    "ExperimentSpec",
    "LastRunOut",
    "RunStart",
    "TotalsOut",
]


class ScoreStatOut(ApiModel):
    # The mean of a numeric score (booleans count 1 or 0); null for a categorical one.
    mean: float | None
    count: int
    # Items per category of a categorical score; null for a numeric one.
    categories: dict[str, int] | None


class TotalsOut(ApiModel):
    items: int
    errors: int
    # The sum of the known item costs; null when no item's cost is known.
    cost_usd: Money | None
    # True when an item that made a call has no known cost: `cost_usd` is then a lower bound.
    has_unpriced: bool
    # Nearest-rank percentiles over the items that answered; null without any.
    latency_p50_ms: float | None
    latency_p95_ms: float | None
    scores: dict[str, ScoreStatOut]

    @classmethod
    def of(cls, totals: Totals) -> "TotalsOut":
        return cls(
            items=totals.items,
            errors=totals.errors,
            cost_usd=totals.cost_usd,
            has_unpriced=totals.has_unpriced,
            latency_p50_ms=totals.latency_p50_ms,
            latency_p95_ms=totals.latency_p95_ms,
            scores={
                name: ScoreStatOut(mean=stat.mean, count=stat.count, categories=stat.categories)
                for name, stat in totals.scores.items()
            },
        )


class ExperimentRunOut(ApiModel):
    id: uuid.UUID
    experiment_id: uuid.UUID
    # 1, 2, 3… within the experiment, in the order the runs were started; never changes.
    number: int
    # The target this run used: the experiment's, or its prompt at an overriding version.
    target: Target
    # queued | running | done | failed
    status: str
    # Why a run failed: cost_cap_reached, abandoned, cancelled, model_unpriced, …
    error: str | None
    started_at: datetime
    finished_at: datetime | None
    heartbeat_at: datetime | None
    started_by: uuid.UUID | None
    # Stored when the run ended (null before). A single run's read computes them live while the
    # run is unfinished.
    totals: TotalsOut | None = None

    @classmethod
    def of(cls, run: ExperimentRun, totals: Totals | None = None) -> "ExperimentRunOut":
        """`totals` overrides the stored ones (a live computation)."""
        return cls(
            id=run.id,
            experiment_id=run.experiment_id,
            number=run.number,
            target=parse_target(run.target),
            status=run.status,
            error=run.error,
            started_at=run.started_at,
            finished_at=run.finished_at,
            heartbeat_at=run.heartbeat_at,
            started_by=run.started_by,
            totals=_totals_out(run, totals),
        )


def _totals_out(run: ExperimentRun, totals: Totals | None) -> TotalsOut | None:
    if totals is not None:
        return TotalsOut.of(totals)
    return TotalsOut.of(totals_from_json(run.totals)) if run.totals is not None else None


class LastRunOut(ApiModel):
    """An experiment's latest run, for its list row."""

    id: uuid.UUID
    number: int
    status: str
    started_at: datetime
    finished_at: datetime | None
    # Stored when the run ended; null while it is unfinished.
    totals: TotalsOut | None

    @classmethod
    def of(cls, run: ExperimentRun) -> "LastRunOut":
        return cls(
            id=run.id,
            number=run.number,
            status=run.status,
            started_at=run.started_at,
            finished_at=run.finished_at,
            totals=_totals_out(run, None),
        )


class ExperimentOut(ApiModel):
    id: uuid.UUID
    name: str
    dataset_id: uuid.UUID
    config: ExperimentConfig
    evaluator_ids: list[uuid.UUID]
    created_by: uuid.UUID | None
    created_at: datetime
    run_count: int
    # The run with the highest number; null before the first run.
    last_run: LastRunOut | None

    @classmethod
    def of(cls, experiment: Experiment, summary: RunSummary | None = None) -> "ExperimentOut":
        """`summary` None means the experiment has no runs (it was just created)."""
        last = summary.last_run if summary is not None else None
        return cls.model_validate(
            {
                "id": experiment.id,
                "name": experiment.name,
                "dataset_id": experiment.dataset_id,
                "config": experiment.config,
                "evaluator_ids": list(experiment.evaluator_ids or ()),
                "created_by": experiment.created_by,
                "created_at": experiment.created_at,
                "run_count": summary.run_count if summary is not None else 0,
                "last_run": LastRunOut.of(last) if last is not None else None,
            }
        )


class ExperimentItemOut(ApiModel):
    dataset_item_id: uuid.UUID
    # Null when the call never reached the gateway or its span was not stored.
    trace_id: str | None
    # `{text, finish_reason}`; null when the item failed.
    output: dict[str, Any] | None
    # `{score name: number | boolean | category}`; an evaluator that did not score is absent.
    scores: dict[str, Any]
    latency_ms: float | None
    cost_usd: Money | None
    # `missing_variables: a, b`, `BUDGET_EXCEEDED` or the provider's failure.
    error: str | None
    # Scoring the answer broke (the answer and its cost are kept); its scores are then absent.
    evaluator_error: str | None
    created_at: datetime

    @classmethod
    def of(cls, item: ExperimentItem) -> "ExperimentItemOut":
        return cls(
            dataset_item_id=item.dataset_item_id,
            trace_id=item.trace_id,
            output=item.output,
            scores=item.scores or {},
            latency_ms=item.latency_ms,
            cost_usd=item.cost_usd,
            error=item.error,
            evaluator_error=item.evaluator_error,
            created_at=item.created_at,
        )
