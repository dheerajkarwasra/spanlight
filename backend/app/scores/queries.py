"""Reads, locks and bulk writes for scores and score configs.

`scores`, `score_configs` and `traces` are under row-level security, so every query here runs in
a transaction bound to the project (or with the worker bypass); the explicit `project_id`
filters are a second guard.
"""

import uuid
from collections.abc import Iterable, Sequence
from decimal import Decimal
from typing import Any, NamedTuple

from sqlalchemy import exists, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import new_id
from app.db.models import Score, ScoreConfig, Trace, User

# A trace's scores are listed whole; this bounds one response.
MAX_TRACE_SCORES = 1000

# The explorer shows this many score names per trace.
MAX_SUMMARY_SCORES = 10

# A project holds at most this many score configs (one per score name). Names are meant to be a
# fixed vocabulary; this stops a client that builds names from data from growing the table, and
# lets the config list be returned whole.
MAX_CONFIGS = 500

ScoreRow = tuple[Score, str | None]


class ScoreSummary(NamedTuple):
    """The latest score of one name on a trace, as the trace list shows it."""

    name: str
    value: Decimal | None
    string_value: str | None


async def configs_by_name(
    db: AsyncSession, project_id: uuid.UUID, names: Iterable[str]
) -> dict[str, ScoreConfig]:
    """The project's configs for `names`, freshly read, by name."""
    rows = await db.scalars(
        select(ScoreConfig)
        .where(ScoreConfig.project_id == project_id, ScoreConfig.name.in_(set(names)))
        .execution_options(populate_existing=True)
    )
    return {config.name: config for config in rows}


async def configs_by_id(
    db: AsyncSession, project_id: uuid.UUID, config_ids: Iterable[uuid.UUID]
) -> list[ScoreConfig]:
    """The project's configs among `config_ids`; an id of another project is left out."""
    wanted = set(config_ids)
    if not wanted:
        return []
    rows = await db.scalars(
        select(ScoreConfig).where(ScoreConfig.project_id == project_id, ScoreConfig.id.in_(wanted))
    )
    return list(rows)


async def insert_configs_if_absent(
    db: AsyncSession, project_id: uuid.UUID, configs: Sequence[ScoreConfig]
) -> None:
    """Insert implicit configs; a name another writer created first keeps that writer's."""
    if not configs:
        return
    rows = [
        {
            "id": new_id(),
            "project_id": project_id,
            "name": config.name,
            "data_type": config.data_type,
            "implicit": True,
        }
        for config in configs
    ]
    statement = insert(ScoreConfig).values(rows)
    await db.execute(statement.on_conflict_do_nothing(index_elements=["project_id", "name"]))


async def insert_scores(db: AsyncSession, rows: Sequence[dict[str, Any]]) -> int:
    """Insert score rows; one whose `(project_id, id)` exists is skipped. Returns rows inserted."""
    if not rows:
        return 0
    statement = (
        insert(Score)
        .values(list(rows))
        .on_conflict_do_nothing(index_elements=["project_id", "id"])
        .returning(Score.id)
    )
    inserted = await db.scalars(statement)
    return len(inserted.all())


async def mark_traces_scored(
    db: AsyncSession, project_id: uuid.UUID, trace_ids: Iterable[str]
) -> None:
    """Set `has_scores` on the traces that exist and are not marked yet.

    The rows are locked in `trace_id` order first, the order ingestion locks them in, so two
    writers never take the same traces' locks in opposite orders and deadlock.
    """
    due = (
        select(Trace.trace_id)
        .where(
            Trace.project_id == project_id,
            Trace.trace_id.in_(sorted(set(trace_ids))),
            ~Trace.has_scores,
        )
        .order_by(Trace.trace_id)
        .with_for_update()
        .cte("due")
    )
    await db.execute(
        update(Trace)
        .where(Trace.project_id == project_id, Trace.trace_id.in_(select(due.c.trace_id)))
        .values(has_scores=True)
        .execution_options(synchronize_session=False)
    )


async def trace_exists(db: AsyncSession, project_id: uuid.UUID, trace_id: str) -> bool:
    found = await db.scalar(
        select(Trace.trace_id).where(Trace.project_id == project_id, Trace.trace_id == trace_id)
    )
    return found is not None


async def trace_scores(db: AsyncSession, project_id: uuid.UUID, trace_id: str) -> list[ScoreRow]:
    """A trace's scores with their author's name, latest first."""
    rows = await db.execute(
        select(Score, User.name)
        .outerjoin(User, User.id == Score.author_user_id)
        .where(Score.project_id == project_id, Score.trace_id == trace_id)
        .order_by(Score.created_at.desc(), Score.id.desc())
        .limit(MAX_TRACE_SCORES)
    )
    return [(score, name) for score, name in rows]


async def get_score(
    db: AsyncSession, project_id: uuid.UUID, score_id: uuid.UUID
) -> ScoreRow | None:
    row = (
        await db.execute(
            select(Score, User.name)
            .outerjoin(User, User.id == Score.author_user_id)
            .where(Score.project_id == project_id, Score.id == score_id)
        )
    ).first()
    return None if row is None else (row[0], row[1])


async def lock_score(db: AsyncSession, project_id: uuid.UUID, score_id: uuid.UUID) -> Score | None:
    return await db.scalar(
        select(Score)
        .where(Score.project_id == project_id, Score.id == score_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


async def count_configs(db: AsyncSession, project_id: uuid.UUID) -> int:
    found = await db.scalar(
        select(func.count()).select_from(ScoreConfig).where(ScoreConfig.project_id == project_id)
    )
    return int(found or 0)


async def list_configs(db: AsyncSession, project_id: uuid.UUID) -> list[ScoreConfig]:
    """The project's score configs by name, at most `MAX_CONFIGS` (creation stops at that cap,
    so the list is whole unless concurrent writers raced past it)."""
    rows = await db.scalars(
        select(ScoreConfig)
        .where(ScoreConfig.project_id == project_id)
        .order_by(ScoreConfig.name, ScoreConfig.id)
        .limit(MAX_CONFIGS)
    )
    return list(rows)


async def lock_config(
    db: AsyncSession, project_id: uuid.UUID, config_id: uuid.UUID
) -> ScoreConfig | None:
    return await db.scalar(
        select(ScoreConfig)
        .where(ScoreConfig.project_id == project_id, ScoreConfig.id == config_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


async def name_in_use(db: AsyncSession, project_id: uuid.UUID, name: str) -> bool:
    """Whether any score of the project carries `name`."""
    found = await db.scalar(
        select(exists().where(Score.project_id == project_id, Score.name == name))
    )
    return bool(found)


async def latest_scores_by_name(
    db: AsyncSession, project_id: uuid.UUID, trace_ids: Sequence[str]
) -> dict[str, list[ScoreSummary]]:
    """Each trace's latest score per name, most recently scored first, at most 10 names per trace.

    One query over the page's trace ids: the latest row per (trace, name) is ranked by
    `created_at` then id, and the names of a trace are ranked by how recently they were scored.
    """
    if not trace_ids:
        return {}
    per_name = (
        select(
            Score.trace_id,
            Score.name,
            Score.value,
            Score.string_value,
            Score.created_at,
            func.row_number()
            .over(
                partition_by=(Score.trace_id, Score.name),
                order_by=(Score.created_at.desc(), Score.id.desc()),
            )
            .label("name_rank"),
        )
        .where(Score.project_id == project_id, Score.trace_id.in_(set(trace_ids)))
        .subquery()
    )
    latest = select(per_name).where(per_name.c.name_rank == 1).subquery()
    ranked = (
        select(
            latest,
            func.row_number()
            .over(
                partition_by=latest.c.trace_id,
                order_by=(latest.c.created_at.desc(), latest.c.name),
            )
            .label("trace_rank"),
        )
    ).subquery()
    rows = await db.execute(
        select(ranked.c.trace_id, ranked.c.name, ranked.c.value, ranked.c.string_value)
        .where(ranked.c.trace_rank <= MAX_SUMMARY_SCORES)
        .order_by(ranked.c.trace_id, ranked.c.trace_rank)
    )
    summaries: dict[str, list[ScoreSummary]] = {}
    for trace_id, name, value, string_value in rows:
        summaries.setdefault(trace_id, []).append(ScoreSummary(name, value, string_value))
    return summaries


async def scores_by_run(
    db: AsyncSession, project_id: uuid.UUID, run_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, list[ScoreSummary]]:
    """The scores each evaluator run wrote, by run id, in name order. One query."""
    wanted = set(run_ids)
    if not wanted:
        return {}
    rows = await db.execute(
        select(Score.evaluator_run_id, Score.name, Score.value, Score.string_value)
        .where(Score.project_id == project_id, Score.evaluator_run_id.in_(wanted))
        .order_by(Score.evaluator_run_id, Score.name, Score.created_at)
    )
    summaries: dict[uuid.UUID, list[ScoreSummary]] = {}
    for run_id, name, value, string_value in rows:
        if run_id is not None:
            summaries.setdefault(run_id, []).append(ScoreSummary(name, value, string_value))
    return summaries
