"""The Users page figures against the trace totals, across the refresh window (D13).

The pilot saw per-user cost 10-13% off the trace totals. The refresh job recomputes a window of
recent days only, so a span that lands on an older trace is absorbed only while its trace's day is
still inside that window: three days on every pass, eight on the nightly one. Both Users responses
carry `computed_at`, the latest recompute of the rows they sum, so the page can say how fresh the
figures are.
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx

from app.config import Settings
from app.end_users.days import deep_days, midnight, recent_days
from app.end_users.jobs import refresh_all, run_refresh_user_stats_deep
from app.jobs.scheduler import REFRESH_USER_STATS_DEEP, periodic_jobs
from app.jobs.tasks import TASKS
from tests.helpers import Browser, Workspace, bearer, create_workspace, new_trace_id, span
from tests.jobs.bypass_support import SessionFactory, fetch

USER = "u1"

_TRACE_TOTALS = """
    SELECT count(*) AS traces, sum(span_count) AS llm_calls, sum(cost_usd) AS cost_usd,
           sum(input_tokens + output_tokens) AS tokens
    FROM traces
    WHERE project_id = CAST(:project_id AS uuid) AND external_user_id = :user
"""


def _anchor() -> tuple[date, datetime]:
    """The refresh's "today" and "now": noon of yesterday (UTC), so every span lies in the past
    whatever the wall clock reads (ingestion rejects a span that ends in the future)."""
    today = (datetime.now(UTC) - timedelta(days=1)).date()
    return today, midnight(today) + timedelta(hours=12)


def _window(first: date, last: date) -> dict[str, str]:
    return {
        "from": midnight(first).isoformat(),
        "to": midnight(last + timedelta(days=1)).isoformat(),
    }


async def _ingest(
    client: httpx.AsyncClient, workspace: Workspace, trace_id: str, day: date, minute: int = 0
) -> None:
    """One LLM span of `trace_id` for `USER`, at 10:`minute` UTC on `day`."""
    started = midnight(day) + timedelta(hours=10, minutes=minute)
    body = span(trace_id=trace_id, start=started, trace={"name": "answer", "user_id": USER})
    response = await client.post(
        "/v1/traces", json={"spans": [body]}, headers=bearer(workspace.api_key)
    )
    assert response.status_code == 200 and response.json()["accepted"] == 1, response.text


async def _users(owner: Browser, workspace: Workspace, first: date, last: date) -> dict[str, Any]:
    response = await owner.get(
        f"/api/v1/projects/{workspace.project_id}/users", params=_window(first, last)
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


async def _detail(owner: Browser, workspace: Workspace, first: date, last: date) -> dict[str, Any]:
    response = await owner.get(
        f"/api/v1/projects/{workspace.project_id}/users/{USER}", params=_window(first, last)
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


async def _assert_matches_traces(
    session_factory: SessionFactory, workspace: Workspace, users: dict[str, Any]
) -> None:
    [expected] = await fetch(
        session_factory, _TRACE_TOTALS, {"project_id": workspace.project_id, "user": USER}
    )
    [item] = users["items"]
    assert item["external_user_id"] == USER
    assert (item["traces"], item["llm_calls"], item["tokens"]) == (
        expected["traces"],
        expected["llm_calls"],
        expected["tokens"],
    )
    assert Decimal(item["cost_usd"]) == expected["cost_usd"]


def test_recent_and_deep_days() -> None:
    now = datetime(2026, 10, 16, 0, 30, tzinfo=UTC)
    today = date(2026, 10, 16)
    assert recent_days(now) == [today - timedelta(days=2), today - timedelta(days=1), today]
    assert deep_days(now) == [today - timedelta(days=offset) for offset in range(7, -1, -1)]


async def test_late_span_two_days_back_is_absorbed(
    browser_factory: Any, client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    owner: Browser = await browser_factory("owner@example.com")
    workspace = await create_workspace(owner)
    today, now = _anchor()
    days = [today - timedelta(days=2), today - timedelta(days=1), today]
    trace_ids = {day: new_trace_id() for day in days}
    for day, trace_id in trace_ids.items():
        await _ingest(client, workspace, trace_id, day)

    # Nothing is counted until the refresh runs, and the response says so.
    before = await _users(owner, workspace, days[0], today)
    assert before["items"] == [] and before["computed_at"] is None

    await refresh_all(session_factory, now=now)
    first = await _users(owner, workspace, days[0], today)
    await _assert_matches_traces(session_factory, workspace, first)
    assert first["computed_at"] is not None
    detail = await _detail(owner, workspace, days[0], today)
    assert detail["computed_at"] == first["computed_at"]

    # A span that arrives late for the trace of the day before yesterday.
    await _ingest(client, workspace, trace_ids[days[0]], days[0], minute=5)
    await refresh_all(session_factory, now=now)
    second = await _users(owner, workspace, days[0], today)
    assert datetime.fromisoformat(second["computed_at"]) > datetime.fromisoformat(
        first["computed_at"]
    )
    await _assert_matches_traces(session_factory, workspace, second)


async def test_nightly_pass_absorbs_a_span_a_week_late(
    browser_factory: Any, client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    owner: Browser = await browser_factory("owner@example.com")
    workspace = await create_workspace(owner)
    today, now = _anchor()
    week_ago = today - timedelta(days=7)
    old_trace = new_trace_id()
    await _ingest(client, workspace, old_trace, week_ago)
    await _ingest(client, workspace, new_trace_id(), today)
    await refresh_all(session_factory, now=now, days=deep_days(now))
    await _assert_matches_traces(
        session_factory, workspace, await _users(owner, workspace, week_ago, today)
    )

    await _ingest(client, workspace, old_trace, week_ago, minute=5)
    # The 15-minute pass does not reach a week back; the nightly pass does.
    await refresh_all(session_factory, now=now)
    stale = await _users(owner, workspace, week_ago, today)
    assert stale["items"][0]["llm_calls"] == 2
    await refresh_all(session_factory, now=now, days=deep_days(now))
    await _assert_matches_traces(
        session_factory, workspace, await _users(owner, workspace, week_ago, today)
    )


def test_nightly_pass_is_scheduled_with_the_user_stats(settings: Settings) -> None:
    assert REFRESH_USER_STATS_DEEP.kind == "refresh_user_stats_deep"
    assert REFRESH_USER_STATS_DEEP.every == timedelta(days=1)
    assert REFRESH_USER_STATS_DEEP.max_attempts == 1
    assert REFRESH_USER_STATS_DEEP.run_after_offset == timedelta(hours=2)
    assert TASKS["refresh_user_stats_deep"] is run_refresh_user_stats_deep
    assert REFRESH_USER_STATS_DEEP in periodic_jobs(
        settings.model_copy(update={"user_stats_enabled": True})
    )
    assert REFRESH_USER_STATS_DEEP not in periodic_jobs(
        settings.model_copy(update={"user_stats_enabled": False})
    )
