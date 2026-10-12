"""One-off job that seeds the demo evaluations: the same work as `spanlight demo seed-evals`.

For a hosted demo where running the command inside the api container is not practical. Queue it
by hand, once for the version 1 run and again after that run has ended for the version 2 run:

    INSERT INTO jobs (kind, max_attempts) VALUES ('seed_demo_evals', 1);

The job is never scheduled. It has a time limit so a stuck seed fails with a recorded reason
instead of holding the worker, and a failure (too few traces, no Anthropic credential, the time
limit) is stored in `jobs.last_error`.
"""

import asyncio
from datetime import UTC, datetime
from typing import Any

import structlog

from app.jobs.context import TaskContext
from app.services.demo import ensure_demo_workspace
from app.services.demo_evals import seed_evals

logger = structlog.get_logger(__name__)

SEED_DEMO_EVALS_JOB = "seed_demo_evals"
# Seeding is a few dozen inserts; anything near this long is stuck, not slow.
SEED_TIMEOUT_S = 120


async def run_seed_demo_evals(context: TaskContext, _: dict[str, Any]) -> None:
    async with asyncio.timeout(SEED_TIMEOUT_S):
        async with context.session_factory() as db:
            workspace = await ensure_demo_workspace(db)
            report = await seed_evals(db, workspace, datetime.now(UTC))
            await db.commit()
    logger.info(
        "demo_seed_evals_done",
        created={name: str(object_id) for name, object_id in report.created.items()},
        runs={name: str(run_id) for name, run_id in report.runs.items()},
        items=report.items,
        waiting_for_v1=report.waiting,
        already_seeded=report.already_seeded,
    )
