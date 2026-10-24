"""The token mix (UX Phase 4 spec ruling 3): a project's LLM tokens by price class.

Failure modes: a class summed from the wrong column (the two cache-write durations not added);
a window cut at its exact instants instead of widened to the UTC days it touches, or widened one
day too far when it ends at midnight; a project without rollups reading as zero tokens instead
of unknown; another project's rollups counted.
"""

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from tests.helpers import Workspace, create_workspace
from tests.jobs.bypass_support import SessionFactory, execute

DAY_1, DAY_2, DAY_3 = date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7)

_INSERT = """
    INSERT INTO cost_rollups_daily (
        project_id, day, provider, model, calls, priced_calls, unpriced_calls,
        usage_unreported_calls, cut_stream_calls, cache_hit_calls, unmodelled_modifier_calls,
        uncached_input_tokens, cache_read_tokens, cache_write_5m_tokens, cache_write_1h_tokens,
        output_tokens, web_search_requests, cost_usd, fee_usd
    ) VALUES (
        :project_id, :day, 'openai', :model, 1, 1, 0, 0, 0, 0, 0,
        :uncached, :read, :write_5m, :write_1h, :output, 0, :cost, :fee
    )
"""


async def _seed(
    sessions: SessionFactory,
    project_id: str,
    day: date,
    model: str,
    tokens: tuple[int, int, int, int, int],
    cost: str | None = None,
    fee: str | None = None,
) -> None:
    uncached, read, write_5m, write_1h, output = tokens
    await execute(
        sessions,
        _INSERT,
        {
            "project_id": uuid.UUID(project_id),
            "day": day,
            "model": model,
            "uncached": uncached,
            "read": read,
            "write_5m": write_5m,
            "write_1h": write_1h,
            "output": output,
            "cost": Decimal(cost) if cost else None,
            "fee": Decimal(fee) if fee else None,
        },
    )


async def _mix(workspace: Workspace, start: str, end: str) -> dict[str, Any]:
    response = await workspace.owner.get(
        f"/api/v1/projects/{workspace.project_id}/metrics/token-mix",
        params={"from": start, "to": end},
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


async def test_token_mix_sums_classes_across_models_and_days(
    browser_factory: Any, session_factory: SessionFactory
) -> None:
    workspace = await create_workspace(await browser_factory("owner@example.com"))
    project = workspace.project_id
    await _seed(session_factory, project, DAY_1, "a", (100, 10, 1, 2, 50), "0.01", "0.001")
    await _seed(session_factory, project, DAY_1, "b", (200, 20, 3, 4, 60), "0.02")
    await _seed(session_factory, project, DAY_2, "a", (300, 30, 5, 6, 70), "0.03", "0.002")
    await _seed(session_factory, project, DAY_2, "b", (400, 40, 7, 8, 80))  # unpriced
    await _seed(session_factory, project, DAY_3, "a", (9999, 9, 9, 9, 9), "9", "9")  # outside

    mix = await _mix(workspace, "2026-01-05T00:00:00Z", "2026-01-07T00:00:00Z")

    assert mix == {
        "first_day": "2026-01-05",
        "last_day": "2026-01-06",
        "uncached_input_tokens": 1000,
        "output_tokens": 260,
        "cache_read_tokens": 100,
        "cache_write_tokens": 36,
        "fee_usd": mix["fee_usd"],
        "cost_usd": mix["cost_usd"],
    }
    assert Decimal(mix["fee_usd"]) == Decimal("0.003")
    assert Decimal(mix["cost_usd"]) == Decimal("0.06")


async def test_token_mix_window_touches_partial_days(
    browser_factory: Any, session_factory: SessionFactory
) -> None:
    workspace = await create_workspace(await browser_factory("owner@example.com"))
    project = workspace.project_id
    await _seed(session_factory, project, date(2026, 1, 4), "a", (1, 0, 0, 0, 0))  # before
    await _seed(session_factory, project, DAY_1, "a", (10, 0, 0, 0, 0))
    await _seed(session_factory, project, DAY_3, "a", (100, 0, 0, 0, 0))
    await _seed(session_factory, project, date(2026, 1, 8), "a", (1000, 0, 0, 0, 0))  # after

    mix = await _mix(workspace, "2026-01-05T10:00:00Z", "2026-01-07T02:00:00Z")

    assert (mix["first_day"], mix["last_day"]) == ("2026-01-05", "2026-01-07")
    assert mix["uncached_input_tokens"] == 110


async def test_token_mix_end_at_midnight_excludes_that_day(
    browser_factory: Any, session_factory: SessionFactory
) -> None:
    workspace = await create_workspace(await browser_factory("owner@example.com"))
    project = workspace.project_id
    await _seed(session_factory, project, DAY_2, "a", (10, 0, 0, 0, 0))
    await _seed(session_factory, project, DAY_3, "a", (100, 0, 0, 0, 0))

    mix = await _mix(workspace, "2026-01-05T10:00:00Z", "2026-01-07T00:00:00Z")

    assert (mix["first_day"], mix["last_day"]) == ("2026-01-05", "2026-01-06")
    assert mix["uncached_input_tokens"] == 10


async def test_token_mix_is_null_without_rows(browser_factory: Any) -> None:
    workspace = await create_workspace(await browser_factory("owner@example.com"))

    mix = await _mix(workspace, "2026-01-05T00:00:00Z", "2026-01-06T00:00:00Z")

    assert mix == {
        "first_day": "2026-01-05",
        "last_day": "2026-01-05",
        "uncached_input_tokens": None,
        "output_tokens": None,
        "cache_read_tokens": None,
        "cache_write_tokens": None,
        "fee_usd": None,
        "cost_usd": None,
    }


async def test_token_mix_cross_tenant_reads_nothing(
    browser_factory: Any, session_factory: SessionFactory
) -> None:
    alice = await create_workspace(await browser_factory("alice@example.com"), org_name="Alice")
    bob = await create_workspace(await browser_factory("bob@example.com"), org_name="Bob")
    await _seed(session_factory, bob.project_id, DAY_1, "a", (10, 1, 1, 1, 5), "1", "0.5")

    # Alice's own project: Bob's rollups are filtered out.
    mix = await _mix(alice, "2026-01-05T00:00:00Z", "2026-01-06T00:00:00Z")
    assert mix["uncached_input_tokens"] is None and mix["cost_usd"] is None
    # Bob's project: Alice is not a member.
    response = await alice.owner.get(
        f"/api/v1/projects/{bob.project_id}/metrics/token-mix",
        params={"from": "2026-01-05T00:00:00Z", "to": "2026-01-06T00:00:00Z"},
    )
    assert response.status_code == 404
