"""The abandoned-run reapers fail the stale runs of every project and change nothing else: not a
run that is alive or finished, not the evaluators and experiments around them. They run through
`sample_all`, whose row-level-security bypass is what lets them see every project.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.config import Settings
from app.evals import experiment_queries, reservation
from app.evals.jobs import sample_all
from tests.jobs.bypass_support import SessionFactory, execute, fetch, new_projects, seed_row


def _no_sampling(settings: Settings) -> Settings:
    """Settings under which `sample_all` only reaps; the production bypass is what it runs under."""
    return settings.model_copy(update={"evals_enabled": False})


_EVALUATOR_RUNS = (
    "SELECT r.*, e.project_id FROM evaluator_runs r JOIN evaluators e ON e.id = r.evaluator_id"
)
_EXPERIMENT_RUNS = (
    "SELECT r.*, e.project_id FROM experiment_runs r JOIN experiments e ON e.id = r.experiment_id"
)


async def _evaluator_run(
    session_factory: SessionFactory,
    project_id: uuid.UUID,
    *,
    status: str,
    heartbeat: datetime | None,
    source: str = "online",
    reserved: str = "0",
) -> uuid.UUID:
    row = await seed_row(session_factory, "evaluator_runs", project_id)
    await execute(
        session_factory,
        "UPDATE evaluator_runs SET status = :status, heartbeat_at = :heartbeat, source = :source, "
        "reserved_usd = :reserved, started_at = :started WHERE id = :id",
        {
            "id": row["id"],
            "status": status,
            "heartbeat": heartbeat,
            "source": source,
            "reserved": Decimal(reserved),
            "started": heartbeat or datetime.now(UTC) - timedelta(days=1),
        },
    )
    return uuid.UUID(str(row["id"]))


async def _snapshot(
    session_factory: SessionFactory, sql: str
) -> dict[uuid.UUID, dict[str, object]]:
    return {row["id"]: row for row in await fetch(session_factory, sql)}


async def test_stale_judge_runs_of_every_project_are_failed_and_nothing_else(
    session_factory: SessionFactory, settings: Settings
) -> None:
    now = datetime.now(UTC)
    stale_since = now - timedelta(hours=1)
    a, b = await new_projects(session_factory, 2)
    stale, untouched = {}, []
    for project in (a, b):
        stale[project] = await _evaluator_run(
            session_factory, project, status="running", heartbeat=stale_since, reserved="0.5"
        )
        untouched.append(
            await _evaluator_run(session_factory, project, status="running", heartbeat=now)
        )
        untouched.append(
            await _evaluator_run(session_factory, project, status="done", heartbeat=now)
        )
        # A queued test run has no job and is never reaped while queued.
        untouched.append(
            await _evaluator_run(
                session_factory, project, status="queued", heartbeat=None, source="test"
            )
        )
    evaluators = await _snapshot(session_factory, "SELECT * FROM evaluators")
    before = await _snapshot(session_factory, _EVALUATOR_RUNS)

    run = await sample_all(session_factory, _no_sampling(settings), now=now)

    assert (run.reaped, run.experiment_runs_reaped) == (2, 0)
    after = await _snapshot(session_factory, _EVALUATOR_RUNS)
    for project, run_id in stale.items():
        failed = after[run_id]
        assert failed["project_id"] == project
        assert (failed["status"], failed["error"]) == ("failed", reservation.ABANDONED)
        assert (failed["cost_usd"], failed["reserved_usd"]) == (Decimal("0.5"), Decimal(0))
        assert failed["finished_at"] is not None
    for run_id in untouched:
        assert after[run_id] == before[run_id]
    assert await _snapshot(session_factory, "SELECT * FROM evaluators") == evaluators


async def _experiment_run(
    session_factory: SessionFactory, project_id: uuid.UUID, *, status: str, heartbeat: datetime
) -> uuid.UUID:
    row = await seed_row(session_factory, "experiment_runs", project_id)
    await execute(
        session_factory,
        "UPDATE experiment_runs SET status = :status, heartbeat_at = :heartbeat, "
        "finished_at = CASE WHEN :status = 'done' THEN :heartbeat END WHERE id = :id",
        {"id": row["id"], "status": status, "heartbeat": heartbeat},
    )
    run_id = uuid.UUID(str(row["id"]))
    if status != "done":  # a live run has its job queued; a dead one lost it
        await execute(
            session_factory,
            "INSERT INTO jobs (kind, dedupe_key, status) VALUES ('run_experiment', :key, 'queued')",
            {"key": f"{experiment_queries.RUN_JOB}:{run_id}"},
        )
    return run_id


async def test_stale_experiment_runs_of_every_project_are_failed_and_nothing_else(
    session_factory: SessionFactory, settings: Settings
) -> None:
    now = datetime.now(UTC)
    a, b = await new_projects(session_factory, 2)
    stale, untouched = {}, []
    for project in (a, b):
        stale[project] = await _experiment_run(
            session_factory, project, status="running", heartbeat=now - timedelta(hours=1)
        )
        untouched.append(
            await _experiment_run(session_factory, project, status="done", heartbeat=now)
        )
    # A run with a fresh heartbeat whose job is still queued may yet finish.
    untouched.append(await _experiment_run(session_factory, a, status="running", heartbeat=now))
    before = await _snapshot(session_factory, _EXPERIMENT_RUNS)
    experiments = await _snapshot(session_factory, "SELECT * FROM experiments")

    run = await sample_all(session_factory, _no_sampling(settings), now=now)

    assert (run.reaped, run.experiment_runs_reaped) == (0, 2)
    after = await _snapshot(session_factory, _EXPERIMENT_RUNS)
    for project, run_id in stale.items():
        assert after[run_id]["project_id"] == project
        assert (after[run_id]["status"], after[run_id]["error"]) == (
            "failed",
            experiment_queries.ABANDONED,
        )
    for run_id in untouched:
        assert after[run_id] == before[run_id]
    assert await _snapshot(session_factory, "SELECT * FROM experiments") == experiments
