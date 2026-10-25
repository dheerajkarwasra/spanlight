"""The setup checklist's four steps, each one a one-row lookup in the project's own rows.

The caller runs under `require()`, so row-level security already limits every table here to the
project; the explicit `project_id` filter keeps each query on its project index.
"""

import uuid
from typing import Any

from sqlalchemy import ColumnElement, Select, exists, func, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Budget, Score, Trace
from app.scores.types import ScoreSource

# The tag "Send a test trace" puts on its one trace: it proves the pipeline, not the user's app.
SETUP_TEST_TAG = "spanlight:setup-test"


def _any(query: Select[Any]) -> ColumnElement[bool]:
    """Whether `query` has a row, read through the index its ORDER BY names.

    Not `EXISTS`: Postgres drops ORDER BY inside one and then seq-scans `traces` for a project
    with no match (1.1 s over 5.8M traces), expecting an early hit it never finds.
    """
    return func.coalesce(query.limit(1).scalar_subquery(), False)


async def activation_steps(db: AsyncSession, project_id: uuid.UUID) -> dict[str, bool]:
    """`first_trace`, `session`, `budget` and `evaluation`: whether each step is done."""
    steps = select(
        _any(
            select(true())
            .where(Trace.project_id == project_id, ~Trace.tags.contains([SETUP_TEST_TAG]))
            .order_by(Trace.started_at.desc())
        ).label("first_trace"),
        _any(
            select(true())
            .where(Trace.project_id == project_id, Trace.session_id.is_not(None))
            .order_by(Trace.session_id)
        ).label("session"),
        exists().where(Budget.project_id == project_id).label("budget"),
        # Scores sent with an API key (`api`) are the app's own numbers, not an evaluation. A
        # score whose author was deleted or whose run was pruned (both SET NULL) stops counting.
        # `source != API` matches `scores_project_source_idx` (1020), so this reads only the
        # project's human and eval scores.
        _any(
            select(true())
            .where(
                Score.project_id == project_id,
                Score.source != ScoreSource.API,
                or_(
                    (Score.source == ScoreSource.HUMAN) & Score.author_user_id.is_not(None),
                    (Score.source == ScoreSource.EVAL) & Score.evaluator_run_id.is_not(None),
                ),
            )
            .order_by(Score.source)
        ).label("evaluation"),
    )
    return dict((await db.execute(steps)).mappings().one())
