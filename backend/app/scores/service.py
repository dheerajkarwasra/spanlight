"""The score write path, and deleting one score. Callers commit.

`write_scores` is the only way a score is written, whatever its source: the ingestion API, a
person on the dashboard, an evaluator, an annotation queue or an experiment. For one batch it:

1. reads the configs of the batch's names;
2. infers a config for each name that has none, from the first score with that name that can
   be stored, as long as the project stays within `MAX_CONFIGS` names; a score whose new name
   would pass that cap is rejected;
3. inserts those implicit configs `ON CONFLICT (project_id, name) DO NOTHING` and reads them
   back, so two batches racing to create the same name agree on the type the first one stored;
4. validates every score against its name's config; a score that does not fit is rejected on
   its own, with its index and the reason, and never fails the batch;
5. inserts the valid scores `ON CONFLICT (project_id, id) DO NOTHING`: a retried score is
   counted as accepted but stored once;
6. marks the batch's traces that exist `has_scores`, locking them in `trace_id` order. A score
   may arrive before its trace: the trace's insert at ingest checks for existing scores and the
   upsert ORs the flag in, so a later batch heals it too. A score and its trace committed at the
   same moment can still leave the flag unset; the worker's sweep (`app.scores.repair`, run by
   `rollup_hourly`) sets it within minutes. The explorer's score filters read `scores` directly
   and are not affected.

`has_scores` is not cleared when scores are deleted: it means "has been scored", and clearing it
safely would need every write to lock the trace row.

Payloads are never logged: comments and categorical values can quote the user's data.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import new_id
from app.core.observability import SCORES_WRITTEN
from app.db.models import ScoreConfig
from app.scores import queries
from app.scores.errors import ScoreDeleteForbiddenError, ScoreNotFoundError
from app.scores.schemas import ScoreIn
from app.scores.types import ScoreSource
from app.scores.validation import ValidationFailure, stored_value, validate_score

logger = structlog.get_logger(__name__)

TOO_MANY_NAMES = f"name: too many score names in this project (at most {queries.MAX_CONFIGS})"


@dataclass(frozen=True)
class RejectedScore:
    index: int
    reason: str


@dataclass(frozen=True)
class WriteOutcome:
    # Scores stored now or already stored under the same id (a retry).
    accepted: int
    rejected: list[RejectedScore] = field(default_factory=list)


@dataclass(frozen=True)
class ScoreActor:
    """Who deletes a score: an admin may delete any, anyone else only their own human scores."""

    user_id: uuid.UUID
    is_admin: bool


async def write_scores(
    db: AsyncSession,
    project_id: uuid.UUID,
    scores: Sequence[ScoreIn],
    *,
    source: ScoreSource,
    author_user_id: uuid.UUID | None = None,
    evaluator_run_id: uuid.UUID | None = None,
) -> WriteOutcome:
    """Validate and store a batch of scores in the project bound to the transaction."""
    configs = await _configs_for(db, project_id, scores)
    common = {
        "project_id": project_id,
        "source": source,
        "author_user_id": author_user_id,
        "evaluator_run_id": evaluator_run_id,
    }
    rows: list[dict[str, Any]] = []
    rejected: list[RejectedScore] = []
    for index, score in enumerate(scores):
        config = configs.get(score.name)
        checked = validate_score(score, config)
        if isinstance(checked, ValidationFailure):
            rejected.append(RejectedScore(index=index, reason=checked.reason))
        elif config is None:
            # Valid, but its name got no config: the project is at the name cap.
            rejected.append(RejectedScore(index=index, reason=TOO_MANY_NAMES))
        else:
            rows.append({**common, **_score_columns(score, checked)})
    inserted = await queries.insert_scores(db, rows)
    if rows:
        await queries.mark_traces_scored(db, project_id, (row["trace_id"] for row in rows))
    SCORES_WRITTEN.labels(source=source.value).inc(inserted)
    logger.info(
        "scores_written",
        project_id=str(project_id),
        source=source.value,
        accepted=len(rows),
        inserted=inserted,
        rejected=len(rejected),
    )
    return WriteOutcome(accepted=len(rows), rejected=rejected)


def _score_columns(score: ScoreIn, config: ScoreConfig) -> dict[str, Any]:
    """The `scores` columns of one valid score, apart from those the whole batch shares."""
    return {
        "id": score.id or new_id(),
        "trace_id": score.trace_id,
        "span_id": score.span_id,
        "name": score.name,
        "data_type": config.data_type,
        "value": stored_value(score),
        "string_value": score.string_value,
        "comment": score.comment,
    }


async def _configs_for(
    db: AsyncSession, project_id: uuid.UUID, scores: Sequence[ScoreIn]
) -> dict[str, ScoreConfig]:
    """The config of every name in the batch, creating implicit ones for new names."""
    names = {score.name for score in scores}
    configs = await queries.configs_by_name(db, project_id, names)
    inferred: dict[str, ScoreConfig] = {}
    for score in scores:
        if score.name in configs or score.name in inferred:
            continue
        checked = validate_score(score, None)
        if not isinstance(checked, ValidationFailure):
            inferred[score.name] = checked
    if not inferred:
        return configs
    room = queries.MAX_CONFIGS - await queries.count_configs(db, project_id)
    allowed = list(inferred.values())[: max(room, 0)]
    await queries.insert_configs_if_absent(db, project_id, allowed)
    return await queries.configs_by_name(db, project_id, names)


async def configs_in_project(
    db: AsyncSession, project_id: uuid.UUID, config_ids: Sequence[uuid.UUID]
) -> list[ScoreConfig]:
    """The project's score configs among `config_ids`. For callers outside the scores domain
    (review queues); `db` is bound to the project."""
    return await queries.configs_by_id(db, project_id, config_ids)


async def run_scores(
    db: AsyncSession, project_id: uuid.UUID, run_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, list[queries.ScoreSummary]]:
    """The scores of these evaluator runs, by run id (runs without scores are absent). For
    callers outside the scores domain; `db` is bound to the project."""
    return await queries.scores_by_run(db, project_id, run_ids)


async def delete_score(
    db: AsyncSession, project_id: uuid.UUID, score_id: uuid.UUID, actor: ScoreActor
) -> None:
    """Delete one score. Raises `ScoreNotFoundError` and `ScoreDeleteForbiddenError`.

    A person may delete the scores they wrote on the dashboard; evaluator and API scores, and
    other people's, need an admin.
    """
    score = await queries.lock_score(db, project_id, score_id)
    if score is None:
        raise ScoreNotFoundError
    own = score.source is ScoreSource.HUMAN and score.author_user_id == actor.user_id
    if not (own or actor.is_admin):
        raise ScoreDeleteForbiddenError
    await db.delete(score)
    await db.flush()
    logger.info(
        "score_deleted",
        project_id=str(project_id),
        score_id=str(score_id),
        source=score.source.value,
    )
