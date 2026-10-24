"""The Users list and detail honour `environment`, and the list says how many traces it misses.

The per-user daily stats are kept per environment, so `?environment=` narrows both routes to
one environment and no parameter sums every environment, the traces without one included (they
are counted once in the all-environment total and under no named environment). `pending_traces`
counts the window's user traces that started after the latest recompute of their own day.
"""

from datetime import date, datetime, timedelta
from typing import Any

import httpx

from app.end_users.days import midnight
from app.end_users.jobs import refresh_all
from tests.end_users.test_refresh_lag import _anchor, _window
from tests.helpers import Browser, Workspace, bearer, create_workspace, new_trace_id, span
from tests.jobs.bypass_support import SessionFactory, execute


async def _ingest(
    client: httpx.AsyncClient,
    workspace: Workspace,
    day: date,
    *,
    user: str | None,
    environment: str | None,
    hour: int = 10,
    session_id: str | None = None,
) -> None:
    """One trace with one LLM span (1,500 tokens) at `hour`:00 UTC on `day`."""
    trace: dict[str, Any] = {"name": "answer"}
    for field, value in (
        ("user_id", user),
        ("environment", environment),
        ("session_id", session_id),
    ):
        if value is not None:
            trace[field] = value
    started = midnight(day) + timedelta(hours=hour)
    body = span(trace_id=new_trace_id(), start=started, trace=trace)
    response = await client.post(
        "/v1/traces", json={"spans": [body]}, headers=bearer(workspace.api_key)
    )
    assert response.status_code == 200 and response.json()["accepted"] == 1, response.text


async def _get(
    owner: Browser, path: str, day: date, first: date | None = None, **params: str
) -> httpx.Response:
    return await owner.get(path, params={**_window(first or day, day), **params})


async def _users(
    owner: Browser, workspace: Workspace, day: date, first: date | None = None, **params: str
) -> dict[str, Any]:
    path = f"/api/v1/projects/{workspace.project_id}/users"
    response = await _get(owner, path, day, first, **params)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _traces_by_user(body: dict[str, Any]) -> dict[str, int]:
    return {item["external_user_id"]: item["traces"] for item in body["items"]}


async def _seeded(
    browser_factory: Any, client: httpx.AsyncClient, session_factory: SessionFactory
) -> tuple[Browser, Workspace, date, datetime]:
    owner: Browser = await browser_factory("owner@example.com")
    workspace = await create_workspace(owner)
    today, now = _anchor()
    await _ingest(client, workspace, today, user="alice", environment="prod", session_id="s-a")
    await _ingest(client, workspace, today, user="bob", environment="staging")
    await _ingest(client, workspace, today, user="carol", environment="prod", session_id="s-p")
    await _ingest(client, workspace, today, user="carol", environment="staging", session_id="s-s")
    await refresh_all(session_factory, now=now)
    return owner, workspace, today, now


async def test_users_list_filters_by_environment(
    browser_factory: Any, client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    owner, workspace, today, _ = await _seeded(browser_factory, client, session_factory)

    prod = await _users(owner, workspace, today, environment="prod")
    assert _traces_by_user(prod) == {"alice": 1, "carol": 1}
    [alice] = [item for item in prod["items"] if item["external_user_id"] == "alice"]
    assert (alice["llm_calls"], alice["tokens"]) == (1, 1500)

    every = await _users(owner, workspace, today)
    assert _traces_by_user(every) == {"alice": 1, "bob": 1, "carol": 2}
    [carol] = [item for item in every["items"] if item["external_user_id"] == "carol"]
    assert (carol["llm_calls"], carol["tokens"]) == (2, 3000)

    assert (await _users(owner, workspace, today, environment="dev"))["items"] == []


async def test_user_detail_filters_by_environment(
    browser_factory: Any, client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    owner, workspace, today, _ = await _seeded(browser_factory, client, session_factory)
    path = f"/api/v1/projects/{workspace.project_id}/users/carol"

    prod = await _get(owner, path, today, environment="prod")
    assert prod.status_code == 200, prod.text
    body = prod.json()
    assert body["user"]["traces"] == 1
    assert [(d["day"], d["traces"]) for d in body["daily"]] == [(today.isoformat(), 1)]
    assert [s["session_id"] for s in body["recent_sessions"]] == ["s-p"]

    every = (await _get(owner, path, today)).json()
    assert every["user"]["traces"] == 2
    assert [(d["day"], d["traces"]) for d in every["daily"]] == [(today.isoformat(), 2)]
    assert sorted(s["session_id"] for s in every["recent_sessions"]) == ["s-p", "s-s"]

    alice_in_staging = await _get(
        owner, f"/api/v1/projects/{workspace.project_id}/users/alice", today, environment="staging"
    )
    assert alice_in_staging.status_code == 404, alice_in_staging.text


async def test_users_null_environment_counts_in_all_env_total(
    browser_factory: Any, client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    owner: Browser = await browser_factory("owner@example.com")
    workspace = await create_workspace(owner)
    today, now = _anchor()
    await _ingest(client, workspace, today, user="dave", environment="prod")
    await _ingest(client, workspace, today, user="dave", environment=None)
    await _ingest(client, workspace, today, user="erin", environment=None)
    await refresh_all(session_factory, now=now)
    # A second pass must update the environment-less rows in place, not add more.
    await refresh_all(session_factory, now=now)

    every = await _users(owner, workspace, today)
    assert _traces_by_user(every) == {"dave": 2, "erin": 1}
    assert _traces_by_user(await _users(owner, workspace, today, environment="prod")) == {"dave": 1}
    # `environment=` names the empty environment, not the traces without one.
    assert (await _users(owner, workspace, today, environment=""))["items"] == []


async def test_users_pending_traces_counts_traces_after_computed_at(
    browser_factory: Any, client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    owner: Browser = await browser_factory("owner@example.com")
    workspace = await create_workspace(owner)
    today, now = _anchor()
    older = today - timedelta(days=1)

    # No rollup rows and no traces: nothing to say.
    assert (await _users(owner, workspace, today, older))["pending_traces"] is None

    await _ingest(client, workspace, older, user="alice", environment="prod")
    await _ingest(client, workspace, today, user="alice", environment="prod")
    # Traces but no rollup yet: every user trace of the window is pending.
    before = await _users(owner, workspace, today, older)
    assert (before["computed_at"], before["pending_traces"]) == (None, 2)

    await refresh_all(session_factory, now=now)
    assert (await _users(owner, workspace, today, older))["pending_traces"] == 0

    # Pretend each day was last recomputed at 10:30 of that day; the older day earlier than
    # today, as the nightly pass leaves it. The 10:00 traces are in the figures, so not pending;
    # the 11:00 ones started after their day's recompute, so they are pending.
    await execute(
        session_factory,
        "UPDATE user_stats_daily SET computed_at = (day + time '10:30') AT TIME ZONE 'UTC' "
        "WHERE project_id = CAST(:p AS uuid)",
        {"p": workspace.project_id},
    )
    await _ingest(client, workspace, older, user="carol", environment="prod", hour=11)
    await _ingest(client, workspace, today, user="alice", environment="prod", hour=11)
    await _ingest(client, workspace, today, user="bob", environment="staging", hour=11)
    await _ingest(client, workspace, today, user=None, environment="prod", hour=11)

    every = await _users(owner, workspace, today, older)
    latest = midnight(today) + timedelta(hours=10, minutes=30)
    assert datetime.fromisoformat(every["computed_at"]) == latest
    assert every["pending_traces"] == 3
    prod = await _users(owner, workspace, today, older, environment="prod")
    assert prod["pending_traces"] == 2
    # One day alone: only that day's late trace.
    assert (await _users(owner, workspace, older))["pending_traces"] == 1
