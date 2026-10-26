"""After a scheduled experiment run ends `done`: compare it with its baseline and keep the
project's `experiment_regression` insight in step.

The baseline is the previous `done` run of the same experiment with the same target
(`experiment_queries.baseline_run`); without one there is nothing to compare and nothing changes.
The comparison is the product's own (`experiment_compare`) and each score is judged on its test of
record, on the items unchanged between the runs: exact McNemar for yes/no scores and the sign test
for numeric ones, or the clustered paired z test when the experiment has a `cluster_key`. With `m`
tested scores, a score regressed when it went down (more items down than up; a negative mean
change for the clustered test) and its two-sided p is below 0.05 / m (Bonferroni over the scores).
An exact test is decided on the exact `Fraction`, never the rounded value.

A regression opens (or re-detects) one critical insight per experiment, fingerprinted on the
experiment id, and notifies the project's insight channels on the transition into `open`
(`insights.notify_opened`), all in one transaction bound to the project. A scheduled run with no
regression resolves the open or acknowledged insight; a muted one is left to its mute. The
detector engine never touches this kind: its quiet-resolve is per detector kind. The run itself
has already ended; a failure here is logged and never fails the run or its job.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from fractions import Fraction
from typing import Any

import structlog
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import new_id
from app.db.models import Experiment, ExperimentRun, Insight
from app.db.rls import bind_project
from app.evals import experiment_queries as queries
from app.evals.compare import ScoreDelta
from app.evals.experiment_compare import compare_experiment_runs
from app.evals.runner_build import RunnerDeps
from app.evals.stats import ClusteredTest, PairedTest, exact_binomial_two_sided, quantize_rate
from app.insights import lifecycle_queries
from app.insights.fingerprint import fingerprint
from app.insights.notify import notify_opened
from app.insights.schemas import Certainty, Evidence, FailureLayer, InsightStatus, Severity, Window

logger = structlog.get_logger(__name__)

KIND = "experiment_regression"
SCHEDULE_TRIGGER = "schedule"
_ALPHA = Fraction(1, 20)


def test_of_record(delta: ScoreDelta) -> PairedTest | ClusteredTest | None:
    """The clustered test when the experiment has a cluster key, else McNemar or the sign test."""
    return delta.clustered_test or delta.test


def _p_value(test: PairedTest | ClusteredTest) -> Decimal | None:
    if isinstance(test, ClusteredTest):
        return None if test.p_value is None else quantize_rate(test.p_value)
    return quantize_rate(exact_binomial_two_sided(test.regressed, test.n))


def _went_down(test: PairedTest | ClusteredTest, threshold: Fraction) -> bool:
    if isinstance(test, ClusteredTest):
        return (
            test.z is not None
            and test.z < 0
            and test.p_value is not None
            and test.p_value < float(threshold)
        )
    return (
        test.regressed > test.improved
        and exact_binomial_two_sided(test.regressed, test.n) < threshold
    )


type Regression = tuple[ScoreDelta, PairedTest | ClusteredTest]


def regressed_scores(aggregate: dict[str, ScoreDelta]) -> dict[str, Regression]:
    """The scores that got significantly worse on their test of record, with that test: p below
    0.05 / m where m is the number of scores with a test."""
    tested = {
        name: (delta, test)
        for name, delta in aggregate.items()
        if (test := test_of_record(delta)) is not None
    }
    if not tested:
        return {}
    threshold = _ALPHA / len(tested)
    return {name: pair for name, pair in tested.items() if _went_down(pair[1], threshold)}


async def check_scheduled_run(deps: RunnerDeps, run_id: uuid.UUID, project_id: uuid.UUID) -> None:
    """Compare a scheduled run that just ended `done` with its baseline; open, re-detect or
    resolve the experiment's insight. A run a person started, or one without a baseline, changes
    nothing. Never raises."""
    try:
        async with deps.sessions() as db, db.begin():
            await bind_project(db, project_id)
            await _check(db, deps, run_id, project_id)
    except Exception as error:  # noqa: BLE001 - the run has ended; the check must not undo that
        logger.error(
            "experiment_regression_check_failed",
            run_id=str(run_id),
            project_id=str(project_id),
            error_type=type(error).__name__,
        )


async def _check(
    db: AsyncSession, deps: RunnerDeps, run_id: uuid.UUID, project_id: uuid.UUID
) -> None:
    run = await db.get(ExperimentRun, run_id)
    if run is None or run.trigger != SCHEDULE_TRIGGER:
        return
    experiment = await queries.get_experiment(db, project_id, run.experiment_id)
    baseline = await queries.baseline_run(db, run)
    if experiment is None or baseline is None:
        return
    comparison = await compare_experiment_runs(
        db,
        project_id,
        experiment.id,
        baseline.id,
        run.id,
        limit=deps.settings.experiment_max_items,
    )
    regressions = regressed_scores(comparison.aggregate)
    now = deps.clock()
    key = fingerprint(project_id, KIND, str(experiment.id))
    if not regressions:
        resolved = await _resolve(db, project_id, key, now)
        logger.info(
            "experiment_regression_checked",
            run_id=str(run.id),
            baseline_run_id=str(baseline.id),
            experiment_id=str(experiment.id),
            project_id=str(project_id),
            scores_tested=sum(1 for d in comparison.aggregate.values() if d.test is not None),
            resolved=resolved,
        )
        return
    row = _row(project_id, key, experiment, baseline, run, regressions, now)
    before = await lifecycle_queries.lock_by_fingerprint(db, project_id, [key])
    written = await lifecycle_queries.upsert_detections(db, [row], now)
    opened = [
        w.id
        for w in written
        if w.status == InsightStatus.OPEN
        and (w.inserted or before.get(key, InsightStatus.OPEN) != InsightStatus.OPEN)
    ]
    notified = await notify_opened(db, project_id, opened, settings=deps.settings)
    logger.warning(
        "experiment_regression_detected",
        run_id=str(run.id),
        baseline_run_id=str(baseline.id),
        experiment_id=str(experiment.id),
        project_id=str(project_id),
        scores=sorted(regressions),
        opened=len(opened),
        notified=notified,
    )


async def _resolve(db: AsyncSession, project_id: uuid.UUID, key: str, now: datetime) -> int:
    result = await db.execute(
        update(Insight)
        .where(
            Insight.project_id == project_id,
            Insight.fingerprint == key,
            Insight.status.in_([InsightStatus.OPEN, InsightStatus.ACKNOWLEDGED]),
        )
        .values(status=InsightStatus.RESOLVED, resolved_at=now)
        .execution_options(synchronize_session=False)
    )
    return int(getattr(result, "rowcount", 0))


def _row(
    project_id: uuid.UUID,
    key: str,
    experiment: Experiment,
    baseline: ExperimentRun,
    run: ExperimentRun,
    regressions: dict[str, Regression],
    now: datetime,
) -> dict[str, Any]:
    """The `insights` row of a first detection, shaped as `insights.service._new_row` shapes a
    detector's (this kind has no catalogue entry: the sensitivity settings list detectors only)."""
    names = ", ".join(sorted(regressions))
    sentences = [
        f"{name}: {delta.regressed} items down, {delta.improved} up ({_named(test)})"
        for name, (delta, test) in sorted(regressions.items())
    ]
    metrics: dict[str, Decimal | int | str | None] = {
        "experiment_id": str(experiment.id),
        "run_a": str(baseline.id),
        "run_b": str(run.id),
        "scores_regressed": len(regressions),
    }
    for name, (delta, test) in regressions.items():
        metrics[f"{name}.test"] = test.kind
        metrics[f"{name}.improved"] = delta.improved
        metrics[f"{name}.regressed"] = delta.regressed
        metrics[f"{name}.p_value"] = _p_value(test)
        if isinstance(test, ClusteredTest):
            metrics[f"{name}.clusters"] = test.clusters
    evidence = Evidence(
        metrics=metrics,
        window=Window(start=baseline.started_at, end=run.finished_at or now),
    )
    return {
        "id": new_id(),
        "project_id": project_id,
        "kind": KIND,
        "severity": Severity.CRITICAL,
        "status": InsightStatus.OPEN,
        "fingerprint": key,
        "title": f"{names} regressed on the weekly run of {experiment.name}",
        "summary": (
            f"Run #{run.number} scored worse than run #{baseline.number} on the same items with "
            f"the same target. {' '.join(sentences)}."
        ),
        "failure_layer": FailureLayer.PROVIDER.value,
        "certainty": Certainty.MEASURED.value,
        "evidence": evidence.model_dump(mode="json"),
        "suggested_fix": (
            "Compare the two runs item by item. The target did not change, so either the "
            "provider changed what the model name serves or a judge drifted; a heuristic "
            "evaluator on the same experiment tells the two apart."
        ),
        "verification": (
            "The next weekly run shows no significant regression against its baseline; the "
            "finding then resolves on its own."
        ),
        "first_seen_at": now,
        "last_seen_at": now,
        "occurrences": 1,
        "peak_strength": None,
    }


def _named(test: PairedTest | ClusteredTest) -> str:
    if isinstance(test, ClusteredTest):
        return f"clustered z over {test.clusters} clusters, p = {_p_value(test)}"
    return f"exact {'McNemar' if test.kind == 'mcnemar' else 'sign test'}, p = {test.p_value}"
