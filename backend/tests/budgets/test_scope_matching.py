"""Which calls a firing `block` budget applies to (ADR 0012).

A `gateway_key` budget blocks the calls of its key only; a `model` budget blocks its model, the
model's snapshots and an alias that a route target resolves to it, never a longer name such as
`gpt-4o-mini`; a `user` budget blocks only calls that name its end user. An in-process call has
no key, so `gateway_key` budgets never apply to it while the others do. When several firing
budgets apply, the refusal names the one with the lowest amount.

Every budget fires from real spend and a real evaluation pass; the calls are refused by the
guard before any provider is reached (the fake provider is not scripted for them).
"""

import json
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

import httpx
import pytest

from app.gateway.context import GatewayRequest
from app.gateway.execute import execute
from app.gateway.trace_headers import TraceHeaders
from tests.budgets.clock import next_pass
from tests.budgets.support import (
    PRICED_MODEL,
    STEP_TOKENS,
    BudgetRef,
    BudgetWorld,
    blocked_spans,
    chat,
    chat_completion,
    create_budget,
    evaluate,
    ingest_spend,
    state_of,
)
from tests.gateway_fixtures import TEST_ORIGIN, GatewayContextFactory
from tests.helpers import bearer

MODEL = "gpt-4o"
SNAPSHOT = "gpt-4o-2024-08-06"
ALIAS = "house-fast"  # the route's OpenAI target sends it upstream as `gpt-4o`
OTHER_MODEL = "gpt-4o-mini"
USER_HEADER = "x-spanlight-user"


@pytest.fixture
async def key_b(budget_world: BudgetWorld, gateway_app: Any) -> AsyncIterator[httpx.AsyncClient]:
    """A client with a second gateway key of the same project and route."""
    workspace = budget_world.workspace
    response = await workspace.owner.post(
        f"/api/v1/projects/{workspace.project_id}/gateway/keys",
        json={"name": "second", "environment": "test"},
    )
    assert response.status_code == 201, response.text
    transport = httpx.ASGITransport(app=gateway_app)
    async with httpx.AsyncClient(
        transport=transport, base_url=TEST_ORIGIN, headers=bearer(response.json()["secret"])
    ) as client:
        yield client


async def call(
    world: BudgetWorld,
    model: str,
    *,
    client: httpx.AsyncClient | None = None,
    user: str | None = None,
    allowed: bool,
) -> httpx.Response:
    """One chat completion of `model`; an allowed one is answered by the fake provider, a
    refused one must not reach it."""
    if allowed:
        world.fake.reply_json(chat_completion(0))
    headers = {USER_HEADER: user} if user is not None else {}
    body = {"model": model, "messages": [{"role": "user", "content": "Hello"}]}
    response = await (client or world.workspace.gw).post(
        "/gw/v1/chat/completions", json=body, headers=headers
    )
    expected = 200 if allowed else 402
    assert response.status_code == expected, (model, user, response.text)
    return response


async def fire(world: BudgetWorld, *budgets: BudgetRef) -> None:
    """Run a pass and check every one of `budgets` is firing."""
    await evaluate(world, next_pass(world.clock))
    for budget in budgets:
        state = await state_of(world, budget)
        assert state is not None and state["state"] == "firing"


async def add_alias(world: BudgetWorld) -> None:
    """Give the route's OpenAI target the alias `ALIAS` → `MODEL`."""
    workspace = world.workspace
    targets = [
        {"credential_id": str(workspace.openai_credential_id), "model_aliases": {ALIAS: MODEL}},
        {"credential_id": str(workspace.anthropic_credential_id)},
    ]
    response = await workspace.owner.put(
        f"{world.project_url}/gateway/routes/{workspace.route_id}",
        json={
            "config": {"targets": targets, "retry": {}, "fallback": {}},
            "expected_version": 1,
        },
    )
    assert response.status_code == 200, response.text


async def test_gateway_key_budget_blocks_its_key_only(
    budget_world: BudgetWorld, key_b: httpx.AsyncClient
) -> None:
    world = budget_world
    key_a = str(world.workspace.gateway_key_id)
    budget = await create_budget(world, scope="gateway_key", scope_id=key_a, name="Key cap")
    assert (await chat(world, scripted_tokens=STEP_TOKENS)).status_code == 200  # key A spends
    await fire(world, budget)

    await call(world, PRICED_MODEL, allowed=False)
    await call(world, PRICED_MODEL, client=key_b, allowed=True)
    assert [row["attributes"]["spanlight.budget.id"] for row in await blocked_spans(world)] == [
        str(budget.id)
    ]


async def test_model_budget_blocks_snapshots_and_aliases_not_longer_names(
    budget_world: BudgetWorld,
) -> None:
    world = budget_world
    await add_alias(world)
    budget = await create_budget(world, scope="model", scope_id=MODEL, name="Model cap")
    await ingest_spend(world, model=MODEL)  # $0.0125 of gpt-4o
    await fire(world, budget)

    for blocked in (MODEL, SNAPSHOT, ALIAS):
        await call(world, blocked, allowed=False)
    await call(world, OTHER_MODEL, allowed=True)
    upstream = [request.json["model"] for request in world.fake.requests]
    assert upstream == [OTHER_MODEL]


async def test_user_budget_blocks_only_that_user(budget_world: BudgetWorld) -> None:
    world = budget_world
    budget = await create_budget(world, scope="user", scope_id="u1", name="User cap")
    await ingest_spend(world, user="u1")
    await fire(world, budget)

    await call(world, MODEL, user="u1", allowed=False)
    await call(world, MODEL, user="u2", allowed=True)
    await call(world, MODEL, allowed=True)


async def in_process_call(
    world: BudgetWorld, gateway_context: GatewayContextFactory
) -> tuple[int, dict[str, Any]]:
    """A call through `execute` with no key (as the demo job makes them): `MODEL` for `u1`."""
    context = await gateway_context(clock=world.clock)
    assert context.key is None
    request = GatewayRequest(
        surface="chat_completions",
        body={"model": MODEL, "messages": [{"role": "user", "content": "Hello"}]},
        stream=False,
        trace=TraceHeaders(user_id="u1"),
    )
    result = await execute(request, context)
    return result.status, json.loads(result.body or b"{}")


@pytest.mark.parametrize(
    ("scope", "scope_id"), [("project", None), ("model", MODEL), ("user", "u1")]
)
async def test_an_in_process_call_is_blocked_by_project_model_and_user_budgets(
    budget_world: BudgetWorld,
    gateway_context: GatewayContextFactory,
    scope: str,
    scope_id: str | None,
) -> None:
    world = budget_world
    budget = await create_budget(world, scope=scope, scope_id=scope_id)
    await ingest_spend(world, model=MODEL, user="u1")
    await fire(world, budget)

    status, body = await in_process_call(world, gateway_context)

    assert status == 402
    assert body["error"]["spanlight_code"] == "BUDGET_EXCEEDED"
    assert world.fake.requests == []


async def test_an_in_process_call_ignores_gateway_key_budgets(
    budget_world: BudgetWorld, gateway_context: GatewayContextFactory
) -> None:
    world = budget_world
    key_a = str(world.workspace.gateway_key_id)
    budget = await create_budget(world, scope="gateway_key", scope_id=key_a)
    assert (await chat(world, scripted_tokens=STEP_TOKENS)).status_code == 200
    await fire(world, budget)
    await call(world, MODEL, allowed=False)  # firing, and it blocks its own key

    world.fake.reply_json(chat_completion(0))
    status, _ = await in_process_call(world, gateway_context)

    assert status == 200


async def test_the_lowest_matching_budget_is_named(budget_world: BudgetWorld) -> None:
    """Created after the higher one, so a tie-break on id alone would pick the other."""
    world = budget_world
    higher = await create_budget(world, amount=Decimal("0.005"), name="Project cap")
    lowest = await create_budget(
        world, scope="model", scope_id=MODEL, amount=Decimal("0.004"), name="Model cap"
    )
    await ingest_spend(world, model=MODEL)  # $0.0125: over both
    await fire(world, higher, lowest)

    refused = await call(world, SNAPSHOT, allowed=False)

    assert refused.json()["error"]["message"].startswith(
        'Blocked by a Spanlight budget: Budget "Model cap" is exhausted'
    )
    [blocked] = await blocked_spans(world)
    assert blocked["attributes"]["spanlight.budget.id"] == str(lowest.id)
