"""The human-judge pairs agreement is computed over (UX Phase 6, ruling 1).

A pair is one trace with both a judge score and a human score under the evaluator's name. The
judge side is the newest `source = 'eval'` score written by a run of this evaluator (any run
source: online, test or experiment); the human side is the newest `source = 'human'` score on the
same trace. Span ids are ignored. API-key scores (`source = 'api'`) never count. Each evaluator
keeps its newest `limit` pairs by human score time; one row more is read to tell whether the cap
applied. The human side reads `scores_project_name_created_idx`; the judge side starts from this
evaluator's runs (`evaluator_runs_evaluator_started_idx`) and reads their scores through
`scores_evaluator_run_idx`, so neither side grows with other projects' scores or runs.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Evaluator, EvaluatorKind, ScoreConfig


@dataclass
class PairSet:
    """`(human, judge)` values, newest review first; `capped` when older pairs were left out."""

    pairs: list[tuple[Decimal, Decimal]] = field(default_factory=list)
    capped: bool = False


_PAIRS = text(
    """
    WITH ev AS (
        SELECT id, name FROM evaluators
        WHERE project_id = :project_id AND id = ANY(:evaluator_ids)
    ),
    judge AS (
        SELECT DISTINCT ON (ev.id, s.trace_id) ev.id AS evaluator_id, s.trace_id, s.value
        FROM ev
        -- Repeating the ids as a parameter lets the planner estimate these evaluators' runs and
        -- take the evaluator_id index instead of hashing every tenant's runs (no prepared plans).
        JOIN evaluator_runs r ON r.evaluator_id = ev.id AND r.evaluator_id = ANY(:evaluator_ids)
        JOIN scores s
          ON s.evaluator_run_id = r.id
         AND s.project_id = :project_id AND s.name = ev.name AND s.source = 'eval'
        WHERE s.value IS NOT NULL
        ORDER BY ev.id, s.trace_id, s.created_at DESC, s.id DESC
    ),
    human AS (
        SELECT DISTINCT ON (ev.id, s.trace_id)
               ev.id AS evaluator_id, s.trace_id, s.value, s.created_at, s.id
        FROM ev
        JOIN scores s
          ON s.project_id = :project_id AND s.name = ev.name AND s.source = 'human'
        WHERE s.value IS NOT NULL
        ORDER BY ev.id, s.trace_id, s.created_at DESC, s.id DESC
    ),
    paired AS (
        SELECT h.evaluator_id, h.value AS human, j.value AS judge,
               row_number() OVER (
                   PARTITION BY h.evaluator_id ORDER BY h.created_at DESC, h.id DESC
               ) AS rank
        FROM human h
        JOIN judge j ON j.evaluator_id = h.evaluator_id AND j.trace_id = h.trace_id
    )
    SELECT evaluator_id, human, judge FROM paired
    WHERE rank <= :limit + 1
    ORDER BY evaluator_id, rank
    """
)


async def pair_scores(
    db: AsyncSession,
    project_id: uuid.UUID,
    evaluator_ids: Sequence[uuid.UUID],
    limit_per_evaluator: int,
) -> dict[uuid.UUID, PairSet]:
    """Every asked-for evaluator of the project gets an entry, empty when it has no pairs."""
    result: dict[uuid.UUID, PairSet] = {evaluator_id: PairSet() for evaluator_id in evaluator_ids}
    rows = await db.execute(
        _PAIRS,
        {
            "project_id": project_id,
            "evaluator_ids": list(evaluator_ids),
            "limit": limit_per_evaluator,
        },
    )
    for evaluator_id, human, judge in rows:
        found = result[evaluator_id]
        if len(found.pairs) < limit_per_evaluator:
            found.pairs.append((human, judge))
        else:
            found.capped = True
    return result


async def judges(db: AsyncSession, project_id: uuid.UUID) -> list[Evaluator]:
    """The project's LLM-judge evaluators by name: the only ones agreement is shown for."""
    rows = await db.scalars(
        select(Evaluator)
        .where(Evaluator.project_id == project_id, Evaluator.kind == EvaluatorKind.LLM_JUDGE)
        .order_by(Evaluator.name)
    )
    return list(rows)


async def score_configs(
    db: AsyncSession, project_id: uuid.UUID, names: Sequence[str]
) -> dict[str, ScoreConfig]:
    """The configs of these score names; a name without one is absent."""
    rows = await db.scalars(
        select(ScoreConfig).where(ScoreConfig.project_id == project_id, ScoreConfig.name.in_(names))
    )
    return {config.name: config for config in rows}
