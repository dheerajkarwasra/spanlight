"""Experiment reads and the writes the run job makes.

Experiments, their runs and items are read in a transaction bound to the project, so row-level
security filters them; the `project_id` and parent-id conditions are the second line. The reaper
reads every project when its caller bypasses row-level security.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Text as SqlText
from sqlalchemy import and_, cast, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.db.models import (
    Evaluator,
    Experiment,
    ExperimentItem,
    ExperimentRun,
    ExperimentRunStatus,
    Project,
)
from app.evals.experiment_totals import ItemFacts
from app.jobs.queue import job_may_still_run

RUN_JOB = "run_experiment"
ABANDONED = "abandoned"
# A running run whose heartbeat is this old lost its worker.
ABANDONED_AFTER = timedelta(minutes=10)
_ACTIVE = (ExperimentRunStatus.QUEUED.value, ExperimentRunStatus.RUNNING.value)


def run_job_key(run_id: uuid.UUID) -> str:
    return f"{RUN_JOB}:{run_id}"


async def list_experiments(
    db: AsyncSession,
    project_id: uuid.UUID,
    *,
    after: tuple[datetime, uuid.UUID] | None,
    limit: int,
) -> list[Experiment]:
    """Newest first, up to `limit + 1` rows so the caller sees whether another page follows."""
    query = (
        select(Experiment)
        .where(Experiment.project_id == project_id)
        .order_by(Experiment.created_at.desc(), Experiment.id.desc())
        .limit(limit + 1)
    )
    if after is not None:
        moment, last_id = after
        query = query.where(
            or_(
                Experiment.created_at < moment,
                and_(Experiment.created_at == moment, Experiment.id < last_id),
            )
        )
    return list(await db.scalars(query))


async def get_experiment(
    db: AsyncSession, project_id: uuid.UUID, experiment_id: uuid.UUID, *, lock: bool = False
) -> Experiment | None:
    query = select(Experiment).where(
        Experiment.project_id == project_id, Experiment.id == experiment_id
    )
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    return await db.scalar(query)


async def get_run(
    db: AsyncSession, experiment_id: uuid.UUID, run_id: uuid.UUID
) -> ExperimentRun | None:
    return await db.scalar(
        select(ExperimentRun).where(
            ExperimentRun.experiment_id == experiment_id, ExperimentRun.id == run_id
        )
    )


@dataclass(frozen=True, slots=True)
class RunSummary:
    """How many runs an experiment has, and its latest one (None without runs)."""

    run_count: int
    last_run: ExperimentRun | None


async def run_summaries(
    db: AsyncSession, experiment_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, RunSummary]:
    """For each experiment, its run count and latest run, in one query: every run is numbered
    within its experiment by a window and only the highest number is kept."""
    if not experiment_ids:
        return {}
    ranked = (
        select(
            ExperimentRun,
            func.count().over(partition_by=ExperimentRun.experiment_id).label("run_count"),
            func.row_number()
            .over(
                partition_by=ExperimentRun.experiment_id,
                order_by=ExperimentRun.number.desc(),
            )
            .label("rank"),
        )
        .where(ExperimentRun.experiment_id.in_(list(experiment_ids)))
        .subquery()
    )
    latest = aliased(ExperimentRun, ranked)
    rows = await db.execute(select(latest, ranked.c.run_count).where(ranked.c.rank == 1))
    found = {run.experiment_id: RunSummary(int(count), run) for run, count in rows}
    return {key: found.get(key, RunSummary(0, None)) for key in experiment_ids}


async def scheduled_experiments(db: AsyncSession) -> list[tuple[uuid.UUID, uuid.UUID, uuid.UUID]]:
    """`(experiment_id, project_id, org_id)` of every experiment with a schedule, oldest first.
    Every project: the caller is the `schedule_experiments` job and bypasses row-level
    security."""
    rows = await db.execute(
        select(Experiment.id, Experiment.project_id, Project.org_id)
        .join(Project, Project.id == Experiment.project_id)
        .where(Experiment.schedule.is_not(None))
        .order_by(Experiment.created_at, Experiment.id)
    )
    return [(row.id, row.project_id, row.org_id) for row in rows]


async def baseline_run(db: AsyncSession, run: ExperimentRun) -> ExperimentRun | None:
    """The previous `done` run of the run's experiment with the same target, or None. A run of
    another prompt version is not a baseline."""
    return await db.scalar(
        select(ExperimentRun)
        .where(
            ExperimentRun.experiment_id == run.experiment_id,
            ExperimentRun.id != run.id,
            ExperimentRun.status == ExperimentRunStatus.DONE.value,
            ExperimentRun.target == run.target,
            ExperimentRun.number < run.number,
        )
        .order_by(ExperimentRun.number.desc())
        .limit(1)
    )


async def next_run_number(db: AsyncSession, experiment_id: uuid.UUID) -> int:
    """The number of the experiment's next run. The caller holds the experiment's row lock."""
    highest = await db.scalar(
        select(func.max(ExperimentRun.number)).where(ExperimentRun.experiment_id == experiment_id)
    )
    return int(highest or 0) + 1


async def list_runs(
    db: AsyncSession,
    experiment_id: uuid.UUID,
    *,
    after: tuple[datetime, uuid.UUID] | None,
    limit: int,
) -> list[ExperimentRun]:
    """The experiment's runs, newest first, up to `limit + 1` rows."""
    query = (
        select(ExperimentRun)
        .where(ExperimentRun.experiment_id == experiment_id)
        .order_by(ExperimentRun.started_at.desc(), ExperimentRun.id.desc())
        .limit(limit + 1)
    )
    if after is not None:
        moment, last_id = after
        query = query.where(
            or_(
                ExperimentRun.started_at < moment,
                and_(ExperimentRun.started_at == moment, ExperimentRun.id < last_id),
            )
        )
    return list(await db.scalars(query))


async def list_items(
    db: AsyncSession, run_id: uuid.UUID, *, after: uuid.UUID | None, limit: int
) -> tuple[int, list[ExperimentItem]]:
    """The run's items in dataset item order, up to `limit + 1` rows, with the 1-based position
    of the first one in the run (one count on the `(run_id, dataset_item_id)` key after page 1)."""
    query = (
        select(ExperimentItem)
        .where(ExperimentItem.run_id == run_id)
        .order_by(ExperimentItem.dataset_item_id)
        .limit(limit + 1)
    )
    if after is None:
        return 1, list(await db.scalars(query))
    before = await db.scalar(
        select(func.count()).where(
            ExperimentItem.run_id == run_id, ExperimentItem.dataset_item_id <= after
        )
    )
    rows = await db.scalars(query.where(ExperimentItem.dataset_item_id > after))
    return 1 + int(before or 0), list(rows)


async def list_run_items(
    db: AsyncSession, run_id: uuid.UUID, *, limit: int
) -> list[ExperimentItem]:
    """At most `limit` items of the run in dataset item order (a run holds at most the
    experiment's `max_items`)."""
    return list(
        await db.scalars(
            select(ExperimentItem)
            .where(ExperimentItem.run_id == run_id)
            .order_by(ExperimentItem.dataset_item_id)
            .limit(limit)
        )
    )


async def evaluator_error_counts(
    db: AsyncSession, run_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, int]:
    """Each run's items whose scoring broke; a run without any is absent. One query."""
    if not run_ids:
        return {}
    rows = await db.execute(
        select(ExperimentItem.run_id, func.count())
        .where(
            ExperimentItem.run_id.in_(list(run_ids)), ExperimentItem.evaluator_error.is_not(None)
        )
        .group_by(ExperimentItem.run_id)
    )
    return {run_id: int(count) for run_id, count in rows}


async def item_facts(db: AsyncSession, run_id: uuid.UUID) -> list[ItemFacts]:
    """What the totals need of every stored item of the run (no outputs are read)."""
    rows = await db.execute(
        select(
            ExperimentItem.latency_ms,
            ExperimentItem.cost_usd,
            ExperimentItem.error,
            ExperimentItem.scores,
            ExperimentItem.evaluator_error,
        ).where(ExperimentItem.run_id == run_id)
    )
    return [
        ItemFacts(row.latency_ms, row.cost_usd, row.error, row.scores or {}, row.evaluator_error)
        for row in rows
    ]


async def project_evaluators(
    db: AsyncSession, project_id: uuid.UUID, evaluator_ids: Sequence[uuid.UUID]
) -> list[Evaluator]:
    """The evaluators among `evaluator_ids` that belong to the project, in name order."""
    if not evaluator_ids:
        return []
    return list(
        await db.scalars(
            select(Evaluator)
            .where(Evaluator.project_id == project_id, Evaluator.id.in_(list(evaluator_ids)))
            .order_by(Evaluator.name)
        )
    )


async def insert_item(db: AsyncSession, values: dict[str, Any]) -> None:
    """Store one item of a run; a second write of the same item is ignored."""
    await db.execute(
        insert(ExperimentItem)
        .values(**values)
        .on_conflict_do_nothing(index_elements=["run_id", "dataset_item_id"])
    )


async def touch_run(db: AsyncSession, run_id: uuid.UUID, now: datetime) -> bool:
    """Record that the run is alive; False when it is no longer running (the reaper ended it)."""
    result = await db.execute(
        update(ExperimentRun)
        .where(ExperimentRun.id == run_id, ExperimentRun.status == ExperimentRunStatus.RUNNING)
        .values(heartbeat_at=now)
        .execution_options(synchronize_session=False)
    )
    return int(getattr(result, "rowcount", 0)) == 1


async def finish_run(
    db: AsyncSession,
    run_id: uuid.UUID,
    *,
    status: ExperimentRunStatus,
    error: str | None,
    totals: dict[str, Any],
    now: datetime,
) -> bool:
    """End a running run; False when it had already ended (the reaper won)."""
    result = await db.execute(
        update(ExperimentRun)
        .where(ExperimentRun.id == run_id, ExperimentRun.status == ExperimentRunStatus.RUNNING)
        .values(status=status.value, error=error, totals=totals, finished_at=now)
        .execution_options(synchronize_session=False)
    )
    return int(getattr(result, "rowcount", 0)) == 1


async def reap_abandoned_runs(
    db: AsyncSession, now: datetime, *, experiment_id: uuid.UUID | None = None
) -> int:
    """Fail every unfinished run that can no longer finish, with `abandoned`: a running run whose
    heartbeat is older than `ABANDONED_AFTER`, or any unfinished run whose job is no longer
    queued or running (its worker died with the job's single attempt). Every project when the
    caller bypasses row-level security; one experiment with `experiment_id`. Returns how many."""
    job_alive = job_may_still_run(func.concat(RUN_JOB + ":", cast(ExperimentRun.id, SqlText)))
    stale = and_(
        ExperimentRun.status == ExperimentRunStatus.RUNNING,
        func.coalesce(ExperimentRun.heartbeat_at, ExperimentRun.started_at) < now - ABANDONED_AFTER,
    )
    query = update(ExperimentRun).where(ExperimentRun.status.in_(_ACTIVE), or_(stale, ~job_alive))
    if experiment_id is not None:
        query = query.where(ExperimentRun.experiment_id == experiment_id)
    result = await db.execute(
        query.values(
            status=ExperimentRunStatus.FAILED.value, error=ABANDONED, finished_at=now
        ).execution_options(synchronize_session=False)
    )
    return int(getattr(result, "rowcount", 0))
