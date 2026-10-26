"""Load two runs of one experiment and compare them (`app.evals.compare`).

Both runs must have ended (`done` or `failed`): a run still queued or running has a moving set of
items. Each side loads at most `limit` items, the experiment's `max_items` ceiling. With the
experiment's `cluster_key`, each item is clustered by that key's value in its frozen metadata
(`experiment_totals.cluster_label`).
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ExperimentItem, ExperimentRunStatus
from app.evals import experiment_queries as queries
from app.evals.compare import Comparison, ItemRow, compare_runs
from app.evals.experiment_errors import ExperimentNotFoundError, RunNotFinishedError
from app.evals.experiment_totals import cluster_label
from app.scores.queries import boolean_score_names

_ENDED = (ExperimentRunStatus.DONE.value, ExperimentRunStatus.FAILED.value)


def _row(item: ExperimentItem, cluster_key: str | None) -> ItemRow:
    return ItemRow(
        dataset_item_id=item.dataset_item_id,
        trace_id=item.trace_id,
        output=item.output,
        scores=item.scores or {},
        latency_ms=item.latency_ms,
        cost_usd=item.cost_usd,
        error=item.error,
        evaluator_error=item.evaluator_error,
        input=item.input,
        expected_output=item.expected_output,
        # A JSON-null input reads back as None too; the metadata is non-NULL on every copy.
        has_copy=item.input is not None or item.item_metadata is not None,
        cluster=None
        if cluster_key is None
        else cluster_label((item.item_metadata or {}).get(cluster_key), item.dataset_item_id),
    )


async def compare_experiment_runs(
    db: AsyncSession,
    project_id: uuid.UUID,
    experiment_id: uuid.UUID,
    run_a: uuid.UUID,
    run_b: uuid.UUID,
    *,
    limit: int,
) -> Comparison:
    """Raises `ExperimentNotFoundError` when the experiment or either run is not found under it,
    and `RunNotFinishedError` when either run has not ended."""
    experiment = await queries.get_experiment(db, project_id, experiment_id)
    if experiment is None:
        raise ExperimentNotFoundError
    first = await queries.get_run(db, experiment.id, run_a)
    second = first if run_b == run_a else await queries.get_run(db, experiment.id, run_b)
    if first is None or second is None:
        raise ExperimentNotFoundError
    if first.status not in _ENDED or second.status not in _ENDED:
        raise RunNotFinishedError
    items_a = await queries.list_run_items(db, first.id, limit=limit)
    items_b = (
        items_a
        if second.id == first.id
        else await queries.list_run_items(db, second.id, limit=limit)
    )
    key = experiment.config.get("cluster_key")
    return compare_runs(
        [_row(i, key) for i in items_a],
        [_row(i, key) for i in items_b],
        boolean_scores=await boolean_score_names(db, project_id),
    )
