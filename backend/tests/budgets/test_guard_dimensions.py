"""Which calls a firing `metadata` or `trace_name` block budget applies to (spec §8.3).

The guard matches a `metadata` budget's pair against the `x-spanlight-metadata` header and a
`trace_name` budget against `x-spanlight-trace-name`. When the call joins a stored trace
(`x-spanlight-trace-id`) and the header lacks the value, the stored trace's name and metadata
count too, with the header winning per key. A trace not yet stored gives nothing, so the first
call of a run is not blocked by its name. A call that starts a new trace is named
`"<surface> <model>"`, which a budget on that exact name matches. In-process callers keep
today's project, model and user check.
"""

import dataclasses
import json
from collections.abc import Mapping
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.budgets.guard import StateBudgetGuard
from app.gateway.budget import BudgetDecision
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
    chat_completion,
    create_budget,
    evaluate,
    state_of,
)
from tests.gateway_fixtures import GatewayContextFactory
from tests.gateway_support import read_gateway_spans, read_gateway_traces
from tests.helpers import new_trace_id, span

METADATA_HEADER = "x-spanlight-metadata"
TRACE_NAME_HEADER = "x-spanlight-trace-name"
TRACE_ID_HEADER = "x-spanlight-trace-id"
ACME = {"customer_id": "acme"}
DEFAULT_NAME = f"chat_completions {PRICED_MODEL}"
SNAPSHOT = f"{PRICED_MODEL}-2025-04-14"  # what OpenAI echoes for a request of `PRICED_MODEL`


async def stored_trace(
    world: BudgetWorld, *, name: str = "answer", metadata: Mapping[str, str] | None = None
) -> str:
    """One stored trace named `name` carrying `metadata`, with one $0.002 span; its id."""
    trace_id = new_trace_id()
    usage = {"input_tokens": STEP_TOKENS, "output_tokens": 0, "cached_tokens": 0}
    trace = {"name": name, "environment": "prod", "metadata": dict(metadata or {})}
    body = span(
        trace_id=trace_id,
        start=world.clock.wall() - timedelta(seconds=1),
        model=PRICED_MODEL,
        usage=usage,
        trace=trace,
    )
    response = await world.ingest.post("/v1/traces", json={"spans": [body]})
    assert response.status_code == 200, response.text
    assert response.json()["accepted"] == 1
    return trace_id


async def fire(world: BudgetWorld, *budgets: BudgetRef) -> None:
    await evaluate(world, next_pass(world.clock))
    for budget in budgets:
        state = await state_of(world, budget)
        assert state is not None and state["state"] == "firing"


async def call(
    world: BudgetWorld,
    *,
    metadata: Mapping[str, str] | None = None,
    trace_name: str | None = None,
    joining: str | None = None,
    allowed: bool,
) -> httpx.Response:
    """One chat completion with the given headers; an allowed one is answered by the fake
    provider, a refused one must not reach it."""
    if allowed:
        world.fake.reply_json(chat_completion(0))
    headers = {}
    if metadata is not None:
        headers[METADATA_HEADER] = json.dumps(metadata)
    if trace_name is not None:
        headers[TRACE_NAME_HEADER] = trace_name
    if joining is not None:
        headers[TRACE_ID_HEADER] = joining
    body = {"model": PRICED_MODEL, "messages": [{"role": "user", "content": "Hello"}]}
    response = await world.workspace.gw.post("/gw/v1/chat/completions", json=body, headers=headers)
    assert response.status_code == (200 if allowed else 402), (headers, response.text)
    return response


async def acme_budget(world: BudgetWorld, **extra: Any) -> BudgetRef:
    budget = await create_budget(
        world, scope="metadata", scope_id="customer_id=acme", name="Acme", **extra
    )
    await stored_trace(world, metadata=ACME)
    return budget


async def test_header_metadata_matches(budget_world: BudgetWorld) -> None:
    world = budget_world
    budget = await acme_budget(world)
    await fire(world, budget)

    await call(world, metadata=ACME, allowed=False)
    await call(world, metadata={"plan": "free", **ACME}, allowed=False)
    await call(world, metadata={"customer_id": "globex"}, allowed=True)
    await call(world, allowed=True)
    assert [row["attributes"]["spanlight.budget.id"] for row in await blocked_spans(world)] == [
        str(budget.id)
    ] * 2


async def test_stored_trace_metadata_matches_a_joining_call_without_the_header(
    budget_world: BudgetWorld,
) -> None:
    world = budget_world
    budget = await acme_budget(world)
    acme = await stored_trace(world, metadata=ACME)
    globex = await stored_trace(world, metadata={"customer_id": "globex"})
    await fire(world, budget)

    await call(world, joining=acme, allowed=False)
    await call(world, joining=globex, allowed=True)


async def test_header_wins_over_stored_per_key(budget_world: BudgetWorld) -> None:
    world = budget_world
    by_customer = await acme_budget(world)
    by_plan = await create_budget(world, scope="metadata", scope_id="plan=free", name="Plan")
    acme = await stored_trace(world, metadata=ACME)
    globex = await stored_trace(world, metadata={"customer_id": "globex", "plan": "free"})
    await fire(world, by_customer, by_plan)

    # The header's value replaces the stored one for its key, so the pair no longer matches.
    await call(world, joining=acme, metadata={"customer_id": "globex"}, allowed=True)
    # A stored key the header does not mention still counts.
    refused = await call(world, joining=globex, metadata={"customer_id": "globex"}, allowed=False)
    assert 'Budget "Plan" is exhausted' in refused.json()["error"]["message"]


async def test_trace_name_matches_via_header_and_via_stored_name(
    budget_world: BudgetWorld,
) -> None:
    world = budget_world
    answers = await create_budget(world, scope="trace_name", scope_id="answer", name="Answers")
    surface = await create_budget(world, scope="trace_name", scope_id=DEFAULT_NAME, name="Chat")
    answer = await stored_trace(world, name="answer")
    await stored_trace(world, name=DEFAULT_NAME)
    await fire(world, answers, surface)

    await call(world, trace_name="answer", allowed=False)
    await call(world, joining=answer, allowed=False)
    await call(world, joining=answer, trace_name="other", allowed=True)
    # A new trace is named "<surface> <model>": the budget on that name matches (spec §12).
    refused = await call(world, allowed=False)
    assert 'Budget "Chat" is exhausted' in refused.json()["error"]["message"]
    await call(world, trace_name="other", allowed=True)


async def test_a_budget_on_the_requested_model_name_holds_when_the_provider_echoes_a_snapshot(
    budget_world: BudgetWorld,
) -> None:
    """The stored trace, the spend measurement and the guard all use the model the client asked
    for (ruling T18-1), so a budget on that name fires from gateway spend and then blocks."""
    world = budget_world
    budget = await create_budget(world, scope="trace_name", scope_id=DEFAULT_NAME, name="Chat")
    world.fake.reply_json({**chat_completion(STEP_TOKENS), "model": SNAPSHOT})
    first = await world.workspace.gw.post(
        "/gw/v1/chat/completions",
        json={"model": PRICED_MODEL, "messages": [{"role": "user", "content": "Hello"}]},
    )
    assert first.status_code == 200, first.text
    [stored] = await read_gateway_traces(world.sessions, world.workspace.project_id)
    [span_row] = await read_gateway_spans(world.sessions, world.workspace.project_id)
    assert (stored["name"], span_row["name"], span_row["model"]) == (
        DEFAULT_NAME,
        f"chat_completions {SNAPSHOT}",
        SNAPSHOT,
    )

    await fire(world, budget)  # measured by the stored name: $0.002 over the $0.001 cap

    await call(world, allowed=False)
    await call(world, trace_name="other", allowed=True)


async def test_first_call_of_an_unstored_trace_is_not_blocked_by_a_trace_name_budget(
    budget_world: BudgetWorld,
) -> None:
    world = budget_world
    answers = await create_budget(world, scope="trace_name", scope_id="answer", name="Answers")
    acme = await acme_budget(world)
    await stored_trace(world, name="answer")
    await fire(world, answers, acme)

    # Nothing is stored under this id yet (an SDK batch not exported): no name, no metadata.
    await call(world, joining=new_trace_id(), allowed=True)


async def test_lowest_amount_wins_among_several(budget_world: BudgetWorld) -> None:
    world = budget_world
    project = await create_budget(world, amount=Decimal("0.005"), name="Project cap")
    acme = await create_budget(
        world, scope="metadata", scope_id="customer_id=acme", amount=Decimal("0.004"), name="Acme"
    )
    answers = await create_budget(
        world, scope="trace_name", scope_id="answer", amount=Decimal("0.003"), name="Answers"
    )
    for _ in range(3):  # $0.006: over all three
        await stored_trace(world, name="answer", metadata=ACME)
    await fire(world, project, acme, answers)

    refused = await call(world, metadata=ACME, trace_name="answer", allowed=False)

    assert refused.json()["error"]["message"].startswith(
        'Blocked by a Spanlight budget: Budget "Answers" is exhausted'
    )
    [blocked] = await blocked_spans(world)
    assert blocked["attributes"]["spanlight.budget.id"] == str(answers.id)


class _Spy:
    """The real guard, remembering the dimensions it was given."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def check(
        self,
        db: AsyncSession,
        project_id: UUID,
        key_id: UUID | None,
        model: str,
        external_user_id: str | None,
        now: datetime,
        target_models: Any = (),
        *,
        metadata: Mapping[str, str] | None = None,
        trace_name: str | None = None,
    ) -> BudgetDecision:
        self.calls.append({"metadata": metadata, "trace_name": trace_name})
        return await StateBudgetGuard().check(
            db, project_id, key_id, model, external_user_id, now, target_models
        )


async def test_in_process_caller_unaffected(
    budget_world: BudgetWorld, gateway_context: GatewayContextFactory
) -> None:
    """A judge or the demo job joins a stored trace with no headers: today's check only, and
    no stored-trace read."""
    world = budget_world
    acme = await acme_budget(world)
    answers = await create_budget(world, scope="trace_name", scope_id="answer", name="Answers")
    stored = await stored_trace(world, name="answer", metadata=ACME)
    await fire(world, acme, answers)
    spy = _Spy()
    context = await gateway_context(clock=world.clock)
    assert context.key is None
    context = dataclasses.replace(context, budget_guard=spy)
    request = GatewayRequest(
        surface="chat_completions",
        body={"model": PRICED_MODEL, "messages": [{"role": "user", "content": "Hello"}]},
        stream=False,
        trace=TraceHeaders(trace_id=stored, metadata=dict(ACME), trace_name="answer"),
    )
    world.fake.reply_json(chat_completion(0))

    result = await execute(request, context)

    assert result.status == 200
    assert spy.calls == [{"metadata": None, "trace_name": None}]
