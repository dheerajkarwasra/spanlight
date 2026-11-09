"""A user's day keeps its tokens unknown when none of its calls reported usage.

Failure mode: a user whose only call failed before the provider answered read "0 tokens" on the
Users page, where the project rule is that an unknown value is NULL, never 0. A call that did
report keeps its count, even beside one that did not.
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx

from app.db.rls import bind_project
from app.end_users.days import DayTotals, total
from app.end_users.refresh import refresh_user_stats
from tests.helpers import bearer, create_workspace, new_trace_id, span
from tests.jobs.bypass_support import SessionFactory, fetch


async def test_unreported_user_day_has_unknown_tokens(
    browser_factory: Any, client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    workspace = await create_workspace(await browser_factory("owner@example.com"))
    start = datetime.now(UTC) - timedelta(minutes=10)
    spans = []
    for user, usages in (
        ("failed", [None]),
        ("mixed", [None, {"input_tokens": 30, "output_tokens": 7}]),
    ):
        trace_id = new_trace_id()
        for usage in usages:
            spans.append(
                span(
                    trace_id=trace_id,
                    start=start,
                    usage=usage,
                    trace={"name": "answer", "user_id": user},
                )
            )
    response = await client.post(
        "/v1/traces", json={"spans": spans}, headers=bearer(workspace.api_key)
    )
    assert response.status_code == 200 and response.json()["rejected"] == [], response.text
    project_id = uuid.UUID(workspace.project_id)

    async with session_factory() as db:
        await bind_project(db, project_id)
        assert await refresh_user_stats(db, project_id, [start.date()]) == 2
        await db.commit()

    rows = await fetch(
        session_factory,
        "SELECT external_user_id, tokens FROM user_stats_daily WHERE project_id = :p "
        "ORDER BY external_user_id",
        {"p": project_id},
    )
    assert rows == [
        {"external_user_id": "failed", "tokens": None},
        {"external_user_id": "mixed", "tokens": 37},
    ]


def test_a_day_without_calls_does_not_make_unknown_tokens_zero() -> None:
    """A day without calls reads 0 tokens, which is true, but it reported nothing: summed with a
    day whose calls reported no usage, the total stays unknown instead of 0."""
    idle = DayTotals(date(2026, 10, 1), 1, 0, 0, 0, Decimal(0), 0)
    unreported = DayTotals(date(2026, 10, 2), 1, 2, 2, 2, None, None)
    reported = DayTotals(date(2026, 10, 3), 1, 1, 0, 0, Decimal("0.01"), 40)

    unknown = total([idle, unreported])
    assert unknown is not None and unknown.tokens is None
    known = total([idle, unreported, reported])
    assert known is not None and known.tokens == 40
    no_calls = total([idle])
    assert no_calls is not None and no_calls.tokens == 0
