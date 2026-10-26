"""Experiments, their runs, run totals and run items.

The body that creates an experiment is owned by the evals domain
(`app.evals.experiment_config`), which validates it; it is re-exported here. Money is a decimal
string; an unknown cost or latency is `null`, never zero.
"""

import uuid
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import PlainSerializer

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
    "Rate",
    "RunStart",
    "ScheduleBody",
    "TotalsOut",
]


class ScheduleBody(ApiModel):
    # `weekly` runs the experiment once per ISO week (when the server enables schedules); null
    # clears the schedule.
    schedule: Literal["weekly"] | None


def _format_rate(value: Decimal) -> str:
    return format(value, "f")


# A rate or p-value as a 4-place decimal string, such as "0.6476".
Rate = Annotated[Decimal, PlainSerializer(_format_rate, return_type=str, when_used="json")]


class ScoreStatOut(ApiModel):
    # The mean of a numeric score (booleans count 1 or 0); null for a categorical one.
    mean: float | None
    count: int
    # Items per category of a categorical score; null for a numeric one.
    categories: dict[str, int] | None
    # Boolean scores only: items that passed and the Wilson 95% interval on the pass rate. Null
    # for numeric and categorical scores, and on totals stored before intervals existed.
    passed: int | None = None
    ci_low: Rate | None = None
    ci_high: Rate | None = None
    # Boolean scores of an experiment with a `cluster_key` only: the clustered (CR0) 95% interval
    # on the pass rate and the number of clusters it rests on. Null otherwise, and on totals
    # stored before clustering existed; the interval is null with fewer than two clusters.
    ci_clustered_low: Rate | None = None
    ci_clustered_high: Rate | None = None
    clusters: int | None = None


class TotalsOut(ApiModel):
    items: int
    # Items whose call failed.
    errors: int
    # Answered items whose scoring broke (`evaluator_error`); 0 on totals stored before this count.
    evaluator_errors: int
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
            evaluator_errors=totals.evaluator_errors,
            cost_usd=totals.cost_usd,
            has_unpriced=totals.has_unpriced,
            latency_p50_ms=totals.latency_p50_ms,
            latency_p95_ms=totals.latency_p95_ms,
            scores={
                name: ScoreStatOut(
                    mean=stat.mean,
                    count=stat.count,
                    categories=stat.categories,
                    passed=stat.passed,
                    ci_low=stat.ci_low,
                    ci_high=stat.ci_high,
                    ci_clustered_low=stat.ci_clustered_low,
                    ci_clustered_high=stat.ci_clustered_high,
                    clusters=stat.clusters,
                )
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
    # `manual` (a person started it) or `schedule` (the weekly rerun).
    trigger: str
    # Stored when the run ended (null before). A single run's read computes them live while the
    # run is unfinished.
    totals: TotalsOut | None = None

    @classmethod
    def of(
        cls,
        run: ExperimentRun,
        totals: Totals | None = None,
        evaluator_errors: int | None = None,
    ) -> "ExperimentRunOut":
        """`totals` overrides the stored ones (a live computation). `evaluator_errors` is the
        count from the items for stored totals that predate it (`legacy_evaluator_errors`)."""
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
            trigger=run.trigger,
            totals=_totals_out(run, totals, evaluator_errors),
        )


def _totals_out(
    run: ExperimentRun, totals: Totals | None, evaluator_errors: int | None
) -> TotalsOut | None:
    if totals is not None:
        return TotalsOut.of(totals)
    if run.totals is None:
        return None
    return TotalsOut.of(totals_from_json(run.totals, evaluator_errors))


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
    def of(cls, run: ExperimentRun, evaluator_errors: int | None = None) -> "LastRunOut":
        return cls(
            id=run.id,
            number=run.number,
            status=run.status,
            started_at=run.started_at,
            finished_at=run.finished_at,
            totals=_totals_out(run, None, evaluator_errors),
        )


class ExperimentOut(ApiModel):
    id: uuid.UUID
    name: str
    dataset_id: uuid.UUID
    config: ExperimentConfig
    evaluator_ids: list[uuid.UUID]
    created_by: uuid.UUID | None
    created_at: datetime
    # `weekly`, or null when the experiment runs only by hand. A schedule only starts runs on a
    # server with EXPERIMENT_SCHEDULES_ENABLED.
    schedule: str | None
    run_count: int
    # The run with the highest number; null before the first run.
    last_run: LastRunOut | None

    @classmethod
    def of(
        cls,
        experiment: Experiment,
        summary: RunSummary | None = None,
        legacy_evaluator_errors: Mapping[uuid.UUID, int] | None = None,
    ) -> "ExperimentOut":
        """`summary` None means the experiment has no runs (it was just created);
        `legacy_evaluator_errors` is `experiments.legacy_evaluator_errors` of its last run."""
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
                "schedule": experiment.schedule,
                "run_count": summary.run_count if summary is not None else 0,
                "last_run": (
                    LastRunOut.of(last, (legacy_evaluator_errors or {}).get(last.id))
                    if last is not None
                    else None
                ),
            }
        )


class ExperimentItemOut(ApiModel):
    dataset_item_id: uuid.UUID
    # The item's 1-based rank by `dataset_item_id` within this run.
    position: int
    # The item as this run saw it; all three are null on a run made before runs kept a copy.
    input: Any | None
    expected_output: Any | None
    item_metadata: Any | None
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
    # `{score name: the judge's reasoning}` from the scores on the item's trace; null when none.
    score_comments: dict[str, str] | None
    created_at: datetime

    @classmethod
    def of(
        cls, item: ExperimentItem, position: int, score_comments: dict[str, str] | None = None
    ) -> "ExperimentItemOut":
        return cls(
            dataset_item_id=item.dataset_item_id,
            position=position,
            input=item.input,
            expected_output=item.expected_output,
            item_metadata=item.item_metadata,
            trace_id=item.trace_id,
            output=item.output,
            scores=item.scores or {},
            latency_ms=item.latency_ms,
            cost_usd=item.cost_usd,
            error=item.error,
            evaluator_error=item.evaluator_error,
            score_comments=score_comments,
            created_at=item.created_at,
        )
