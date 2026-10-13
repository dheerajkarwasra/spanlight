"""How an explanation's reservation settles, for every way the call can end.

The rule (`app.insights.explain_settle`): a row whose call the provider may have billed stays in
the budget (at the usage's cost, else at the reserved worst case); only a call that was never
sent, or that the provider refused with an HTTP error status, releases its reservation.
"""

import asyncio
import contextlib
import dataclasses
from datetime import timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest

from app.gateway.context import GatewayContext
from app.gateway.settle_helpers import drain_settling
from app.insights import explain_service
from app.insights.explain_context import explain_context
from app.insights.job_queries import complete_stale_reservations
from tests.budgets.support import BudgetWorld, firing_budget
from tests.gateway_support import Stall
from tests.insights.explain_support import (
    ANSWER,
    ANSWER_COST,
    ArrivalStall,
    ExplainWorld,
    answer_body,
    explain,
    explanation_rows,
    seed_insight,
    seed_spend,
    worst_case,
)

ROUTE_TIMEOUT_MS = 1000  # the shortest a route allows
REFUSED = {"type": "error", "error": {"type": "invalid_request_error", "message": "bad"}}


def _failed(response: httpx.Response) -> bool:
    return response.status_code == 502 and response.json().get("code") == "EXPLAIN_FAILED"


@pytest.mark.parametrize(
    ("reply", "status", "kept"),
    [
        pytest.param((answer_body(), 200), 201, (ANSWER, ANSWER_COST), id="answer"),
        pytest.param((answer_body(None), 200), 502, (None, ANSWER_COST), id="usage-no-text"),
        pytest.param((REFUSED, 400), 502, None, id="http-4xx-no-usage"),
    ],
)
async def test_explain_settle_matrix(
    explain_world: ExplainWorld,
    reply: tuple[dict[str, Any], int],
    status: int,
    kept: tuple[str | None, Decimal] | None,
) -> None:
    world = explain_world
    project_id = world.workspace.project_id
    insight_id = await seed_insight(world.sessions, project_id, world.clock.wall())
    body, provider_status = reply
    world.fake.reply_json(body, status=provider_status)

    response = await explain(world, project_id, insight_id)

    assert response.status_code == status, response.text
    assert status == 201 or _failed(response)
    assert len(world.fake.requests) == 1
    rows = await explanation_rows(world.sessions, world.org_id)
    if kept is None:
        assert rows == []  # refused with an HTTP status and no usage: the reservation is freed
    else:
        [row] = rows
        assert (row["text"], row["cost_usd"]) == kept
        assert row["completed_at"] is not None


async def test_explain_settle_timeout_after_send_keeps_the_worst_case(
    explain_world: ExplainWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = explain_world
    project_id = world.workspace.project_id
    insight_id = await seed_insight(world.sessions, project_id, world.clock.wall())
    worst = await worst_case(world, project_id, insight_id)

    def short_route(*args: Any, **kwargs: Any) -> GatewayContext:
        context = explain_context(*args, **kwargs)
        route = context.route.model_copy(update={"timeout_ms": ROUTE_TIMEOUT_MS})
        return dataclasses.replace(context, route=route)

    monkeypatch.setattr(explain_service, "explain_context", short_route)
    world.fake.reply_json(answer_body(), before_headers=Stall())  # never answers

    response = await explain(world, project_id, insight_id)

    assert _failed(response), response.text
    [row] = await explanation_rows(world.sessions, world.org_id)
    assert (row["text"], row["cost_usd"]) == (None, worst)
    assert row["completed_at"] is not None


async def test_explain_settle_blocked_call_releases_the_reservation(
    explain_world: ExplainWorld, budget_world: BudgetWorld
) -> None:
    world = explain_world
    await firing_budget(budget_world)  # the project's block budget is spent
    insight_id = await seed_insight(world.sessions, world.workspace.project_id, world.clock.wall())

    response = await explain(world, world.workspace.project_id, insight_id)

    assert _failed(response), response.text
    assert world.fake.requests == []
    assert await explanation_rows(world.sessions, world.org_id) == []


async def test_explain_settle_cancelled_mid_call_keeps_the_worst_case(
    explain_world: ExplainWorld,
) -> None:
    world = explain_world
    project_id = world.workspace.project_id
    insight_id = await seed_insight(world.sessions, project_id, world.clock.wall())
    worst = await worst_case(world, project_id, insight_id)
    stall = ArrivalStall()
    world.fake.reply_json(answer_body(), before_headers=stall)

    request = asyncio.create_task(explain(world, project_id, insight_id))
    async with asyncio.timeout(10):
        await stall.arrived.wait()
    request.cancel()  # the client goes away while the provider is answering
    with contextlib.suppress(asyncio.CancelledError):
        await request
    await drain_settling()

    [row] = await explanation_rows(world.sessions, world.org_id)
    assert (row["text"], row["cost_usd"]) == (None, worst)
    assert row["completed_at"] is not None


async def test_explain_settle_stale_reservation_is_completed_at_worst_case(
    explain_world: ExplainWorld,
) -> None:
    world = explain_world
    project_id = world.workspace.project_id
    now = world.clock.wall()
    insight_id = await seed_insight(world.sessions, project_id, now - timedelta(hours=2))
    worst = Decimal("0.00680000")
    stale = await seed_spend(
        world.sessions, project_id, insight_id, worst, now - timedelta(hours=1), completed=False
    )
    fresh = await seed_spend(world.sessions, project_id, insight_id, worst, now, completed=False)

    completed = await complete_stale_reservations(
        world.sessions, before=now - timedelta(minutes=10), now=now
    )

    assert completed == 1
    rows = {row["id"]: row for row in await explanation_rows(world.sessions, world.org_id)}
    assert (rows[stale]["text"], rows[stale]["cost_usd"]) == (None, worst)
    assert rows[stale]["completed_at"] == now
    assert rows[fresh]["completed_at"] is None  # a call still in flight is left alone
