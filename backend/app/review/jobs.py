"""The review selection jobs: the nightly `select_for_review` and one project's
`select_for_review_project`.

`select_for_review` runs once a UTC day from 01:00 (`SELECT_FOR_REVIEW`, scheduled only with
`REVIEW_SELECTION_ENABLED`). It lists every enabled policy, the one read in this feature that
needs the row-level security bypass, and enqueues one `select_for_review_project` per policy for
the day that just ended, with the same dedupe key a manual run uses, so the nightly run and a
manual run for the same day are one job. Three attempts: the listing is cheap and the children
dedupe.

`select_for_review_project` (also what a manual run enqueues) runs `select_for_project` bound
to its project, in one transaction, and ends `done` / `already_selected` when the day's selection
row already exists. Three attempts: a crashed run leaves nothing behind.
"""

import uuid
from datetime import date
from typing import Any

import structlog

from app.db.rls import bypass_rls
from app.jobs.context import TaskContext
from app.jobs.outcome import JobOutcome
from app.jobs.queue import enqueue
from app.jobs.scheduler import SELECT_FOR_REVIEW
from app.review.calendar import previous_day
from app.review.job_keys import SELECT_PROJECT_JOB, project_job_key
from app.review.selection import select_for_project
from app.review.selection_queries import enabled_policies

__all__ = ["SELECT_FOR_REVIEW", "run_select_for_review", "run_select_for_review_project"]

logger = structlog.get_logger(__name__)

PROJECT_JOB_ATTEMPTS = 3


async def run_select_for_review(context: TaskContext, _: dict[str, Any]) -> None:
    """Enqueue one project selection per enabled policy for the UTC day that just ended."""
    day = previous_day(context.clock())
    created = 0
    async with context.session_factory() as db, db.begin():
        await bypass_rls(db)
        policies = await enabled_policies(db)
        for policy in policies:
            created += await enqueue(
                db,
                SELECT_PROJECT_JOB,
                {"project_id": str(policy.project_id), "day": day.isoformat()},
                dedupe_key=project_job_key(policy.project_id, day),
                max_attempts=PROJECT_JOB_ATTEMPTS,
            )
    logger.info(
        "review_selection_children_enqueued",
        day=day.isoformat(),
        policies=len(policies),
        created=created,
    )


async def run_select_for_review_project(
    context: TaskContext, payload: dict[str, Any]
) -> JobOutcome | None:
    """Select the payload's day into its project's review queue."""
    project_id = uuid.UUID(str(payload["project_id"]))
    day = date.fromisoformat(str(payload["day"]))
    result = await select_for_project(
        context.session_factory,
        project_id,
        day,
        now=context.clock(),
        heartbeat=context.heartbeat,
    )
    logger.info(
        "review_selection_done",
        project_id=str(project_id),
        day=day.isoformat(),
        outcome=result.outcome,
        queued=sum(result.queued.values()),
        candidates=result.candidates,
        skipped_duplicates=result.skipped_duplicates,
        cap_hit=result.cap_hit,
    )
    return JobOutcome.ALREADY_SELECTED if result.outcome == "already_selected" else None
