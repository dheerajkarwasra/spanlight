"""The setup checklist's four steps, each one `EXISTS` over the project's own rows.

The caller runs under `require()`, so row-level security already limits every table here to the
project; the explicit `project_id` filter keeps each query on its project index.
"""

import uuid

from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Budget, Evaluator, EvaluatorRun, Score, Trace
from app.scores.types import ScoreSource

# The tag "Send a test trace" puts on its one trace: it proves the pipeline, not the user's app.
SETUP_TEST_TAG = "spanlight:setup-test"


async def activation_steps(db: AsyncSession, project_id: uuid.UUID) -> dict[str, bool]:
    """`first_trace`, `session`, `budget` and `evaluation`: whether each step is done."""
    steps = select(
        exists()
        .where(Trace.project_id == project_id, ~Trace.tags.contains([SETUP_TEST_TAG]))
        .label("first_trace"),
        exists()
        .where(Trace.project_id == project_id, Trace.session_id.is_not(None))
        .label("session"),
        exists().where(Budget.project_id == project_id).label("budget"),
        # Scores sent with an API key (`api`) are the app's own numbers, not an evaluation. The
        # human side reads `scores_author_user_idx`; the eval side starts from the project's
        # evaluators and reads their runs (`evaluator_runs_evaluator_started_idx`) and those
        # runs' scores (`scores_evaluator_run_idx`), so it is bounded by the project. A score
        # whose author was deleted or whose run was pruned (both SET NULL) stops counting.
        or_(
            exists().where(
                Score.project_id == project_id,
                Score.author_user_id.is_not(None),
                Score.source == ScoreSource.HUMAN,
            ),
            select(Score.id)
            .select_from(Evaluator)
            .join(EvaluatorRun, EvaluatorRun.evaluator_id == Evaluator.id)
            .join(
                Score,
                (Score.evaluator_run_id == EvaluatorRun.id)
                & (Score.source == ScoreSource.EVAL)
                & (Score.project_id == project_id),
            )
            .where(Evaluator.project_id == project_id)
            .exists(),
        ).label("evaluation"),
    )
    return dict((await db.execute(steps)).mappings().one())
