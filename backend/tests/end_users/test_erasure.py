"""Erasing one end user removes every row of theirs in one project and nothing else.

Project A has two users and project B the same external id as A's erased user. Each has a trace
with spans, a score, a dataset item copied from the trace, an experiment item run over it (with
its run trace) and a daily stats row. After the request (`202`) and the job, A's erased user has
no row left in any of those tables, A's other user and B's same id are untouched, the completion
audit event's counts match, and the audit log never holds the raw id, only the keyed hash.
"""

import uuid
from typing import Any

import httpx

from app.config import Settings
from app.end_users.erasure_job import run_erase_end_user, subject_hash
from app.jobs.context import TaskContext
from app.jobs.queue import claim_next, complete
from tests.helpers import Browser, Workspace, bearer, create_workspace, join_with_role, span
from tests.jobs.bypass_support import SessionFactory, execute, fetch

ERASED = "user/with/slashes"
KEPT = "other-user"


async def _seed_user(
    client: httpx.AsyncClient, factory: SessionFactory, workspace: Workspace, user: str
) -> dict[str, str]:
    """One trace with two spans, a score, a dataset item copied from the trace, an experiment
    item over it with a run trace, and a stats row; returns the ids the assertions need."""
    trace_id = uuid.uuid4().hex
    body = [span(trace_id=trace_id, trace={"name": "answer", "user_id": user}) for _ in range(2)]
    response = await client.post(
        "/v1/traces", json={"spans": body}, headers=bearer(workspace.api_key)
    )
    assert response.status_code == 200 and response.json()["accepted"] == 2, response.text
    project = workspace.project_id
    run_trace_id = uuid.uuid4().hex
    dataset_id, item_id = uuid.uuid4(), uuid.uuid4()
    statements = (
        "INSERT INTO scores (id, project_id, trace_id, name, data_type, source, value) "
        "VALUES (gen_random_uuid(), :project, :trace, 'quality', 'boolean', 'api', 1)",
        "INSERT INTO traces (project_id, trace_id, started_at, ended_at) "
        "VALUES (:project, :run_trace, now(), now())",
        "INSERT INTO datasets (id, project_id, name, locked_at) "
        "VALUES (:dataset, :project, :user, now())",
        "INSERT INTO dataset_items (id, dataset_id, input, source_trace_id) "
        """VALUES (:item, :dataset, '{"q": "hi"}', :trace)""",
        "INSERT INTO experiments (id, project_id, name, dataset_id, config) "
        "VALUES (:experiment, :project, :user, :dataset, '{}')",
        "INSERT INTO experiment_runs (id, experiment_id, number, target) "
        "VALUES (:run, :experiment, 1, '{}')",
        "INSERT INTO experiment_items (run_id, dataset_item_id, trace_id) "
        "VALUES (:run, :item, :run_trace)",
        "INSERT INTO user_stats_daily "
        "(project_id, day, external_user_id, traces, llm_calls, errors, unpriced_calls, tokens) "
        "VALUES (:project, current_date, :user, 1, 2, 0, 0, 3000)",
    )
    params = {
        "project": project,
        "trace": trace_id,
        "run_trace": run_trace_id,
        "dataset": dataset_id,
        "item": item_id,
        "experiment": uuid.uuid4(),
        "run": uuid.uuid4(),
        "user": user,
    }
    for statement in statements:
        await execute(factory, statement, params)
    return {"project": project, "trace": trace_id, "run_trace": run_trace_id, "item": str(item_id)}


async def _rows_left(factory: SessionFactory, ids: dict[str, str]) -> dict[str, int]:
    """How many rows of the seeded user each table still holds."""
    queries = {
        "traces": "SELECT count(*) FROM traces WHERE project_id = :project "
        "AND trace_id IN (:trace, :run_trace)",
        "spans": "SELECT count(*) FROM spans WHERE project_id = :project "
        "AND trace_id IN (:trace, :run_trace)",
        "scores": "SELECT count(*) FROM scores WHERE project_id = :project AND trace_id = :trace",
        "dataset_items": "SELECT count(*) FROM dataset_items WHERE source_trace_id = :trace",
        "experiment_items": "SELECT count(*) FROM experiment_items "
        "WHERE dataset_item_id = CAST(:item AS uuid)",
        "user_stats_daily": "SELECT count(*) FROM user_stats_daily "
        "WHERE project_id = :project AND external_user_id = :user",
    }
    return {table: (await fetch(factory, sql, ids))[0]["count"] for table, sql in queries.items()}


async def test_erasing_a_user_removes_only_that_users_rows_in_that_project(
    browser_factory: Any,
    client: httpx.AsyncClient,
    session_factory: SessionFactory,
    settings: Settings,
) -> None:
    owner: Browser = await browser_factory("owner@example.com")
    admin: Browser = await browser_factory("admin@example.com")
    project_a = await create_workspace(owner, org_name="A")
    await join_with_role(owner, admin, project_a.org_id, "admin")
    project_b = await create_workspace(owner, org_name="B")
    erased = await _seed_user(client, session_factory, project_a, ERASED)
    kept = await _seed_user(client, session_factory, project_a, KEPT)
    same_id_elsewhere = await _seed_user(client, session_factory, project_b, ERASED)
    untouched = {"traces": 2, "spans": 2, "scores": 1, "dataset_items": 1}
    untouched |= {"experiment_items": 1, "user_stats_daily": 1}
    assert await _rows_left(session_factory, erased | {"user": ERASED}) == untouched

    response = await admin.post(
        f"/api/v1/projects/{project_a.project_id}/users/erasures",
        json={"external_user_id": ERASED},
    )
    assert response.status_code == 202, response.text
    assert response.json()["status"] == "queued"
    # A second request while the job is queued is a no-op: one job, one run.
    again = await admin.post(
        f"/api/v1/projects/{project_a.project_id}/users/erasures",
        json={"external_user_id": ERASED},
    )
    assert again.status_code == 202, again.text

    queued = await fetch(session_factory, "SELECT count(*) FROM jobs WHERE kind = 'erase_end_user'")
    assert queued[0]["count"] == 1
    async with session_factory() as db:
        job = await claim_next(db)
        while job is not None and job.kind != "erase_end_user":
            job = await claim_next(db)
        assert job is not None
    await run_erase_end_user(
        TaskContext(job=job, settings=settings, session_factory=session_factory), job.payload
    )
    saved = await fetch(session_factory, "SELECT payload -> 'counts' AS counts FROM jobs")
    assert saved[0]["counts"]["traces"] == 2  # a retry would carry on from these
    async with session_factory() as db:
        await complete(db, job)

    gone = dict.fromkeys(untouched, 0)
    assert await _rows_left(session_factory, erased | {"user": ERASED}) == gone
    assert await _rows_left(session_factory, kept | {"user": KEPT}) == untouched
    assert await _rows_left(session_factory, same_id_elsewhere | {"user": ERASED}) == untouched

    # A request after the job finished queues it again (it catches data sent since).
    after = await admin.post(
        f"/api/v1/projects/{project_a.project_id}/users/erasures",
        json={"external_user_id": ERASED},
    )
    assert after.status_code == 202, after.text
    requeued = await fetch(
        session_factory,
        "SELECT status FROM jobs WHERE kind = 'erase_end_user' ORDER BY id",
    )
    assert [row["status"] for row in requeued] == ["queued"]

    digest = subject_hash(settings, uuid.UUID(project_a.project_id), ERASED)
    events = await fetch(
        session_factory,
        "SELECT action, target_id, metadata FROM audit_events "
        "WHERE org_id = :org AND action LIKE 'end_user.%' ORDER BY created_at",
        {"org": project_a.org_id},
    )
    # Every request is audited; the job ran once so far.
    assert [(event["action"], event["target_id"]) for event in events] == [
        ("end_user.erase_request", digest),
        ("end_user.erase_request", digest),
        ("end_user.erase", digest),
        ("end_user.erase_request", digest),
    ]
    counts = events[2]["metadata"]
    assert {key: counts[key] for key in untouched} == untouched
    assert counts["user_budgets_remaining"] == 0
    holding_raw_id = await fetch(
        session_factory,
        "SELECT count(*) FROM audit_events "
        "WHERE target_id = :user OR CAST(metadata AS text) LIKE :needle",
        {"user": ERASED, "needle": f"%{ERASED}%"},
    )
    assert holding_raw_id[0]["count"] == 0
