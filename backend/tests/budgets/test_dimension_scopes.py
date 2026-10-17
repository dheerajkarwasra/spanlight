"""Budgets on a metadata pair or a trace name (spec §8.1, §8.2).

A `metadata` budget's `scope_id` is `<key>=<value>`, split at the first `=`, with the key held
to the metadata key pattern and the value kept as sent; a `trace_name` budget's `scope_id` is the
name, trimmed like the gateway's `x-spanlight-trace-name` header. Either scope makes the
budget's hidden rule read raw spans joined to traces for the whole period, so the spend is the
exact cost of the traces that contain the pair, or carry the name, and nothing else. Firing and
resolving are the existing budget machinery; creating one counts a feature use.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy import text

from app.alerts.rules import metric_filters
from app.alerts.types import MetricFilters
from app.budgets.periods import period_start
from app.budgets.types import BudgetPeriod
from app.core.observability import FEATURE_USED
from app.db.rls import bypass_rls
from tests.budgets.clock import next_pass
from tests.budgets.support import (
    BUDGET_NAME,
    CAP,
    PRICED_MODEL,
    STEP_COST,
    STEP_TOKENS,
    BudgetRef,
    BudgetWorld,
    create_budget,
    evaluate,
    events_of,
    outbox_events,
    state_of,
)
from tests.gateway_support import metric_delta
from tests.helpers import new_trace_id, span

KEY_MAX = "k" * 64
VALUE_MAX = "v" * 256
LONGEST_SCOPE_ID = f"{KEY_MAX}={VALUE_MAX}"  # 64 + 1 + 256 = 321 characters


async def post_budget(world: BudgetWorld, scope: str, scope_id: str) -> httpx.Response:
    body = {
        "name": BUDGET_NAME,
        "scope": scope,
        "scope_id": scope_id,
        "period": "daily",
        "amount_usd": str(CAP),
        "action": "block",
        "channel_ids": [str(world.channel_id)],
    }
    return await world.workspace.owner.post(f"{world.project_url}/budgets", json=body)


async def rule_filters(world: BudgetWorld, budget: BudgetRef) -> dict[str, Any]:
    """The hidden rule's stored `filters`, read past row-level security like the worker."""
    async with world.sessions() as db:
        await bypass_rls(db)
        filters = await db.scalar(
            text("SELECT filters FROM alert_rules WHERE id = :id"), {"id": budget.rule_id}
        )
    assert filters is not None
    return dict(filters)


async def ingest_trace(
    world: BudgetWorld,
    *,
    name: str,
    metadata: dict[str, str],
    start: datetime | None = None,
) -> None:
    """One trace named `name` carrying `metadata`, with one span of `STEP_COST`."""
    started = start or world.clock.wall() - timedelta(seconds=1)
    usage = {"input_tokens": STEP_TOKENS, "output_tokens": 0, "cached_tokens": 0}
    trace = {"name": name, "environment": "prod", "metadata": metadata}
    body = span(
        trace_id=new_trace_id(), start=started, model=PRICED_MODEL, usage=usage, trace=trace
    )
    response = await world.ingest.post("/v1/traces", json={"spans": [body]})
    assert response.status_code == 200, response.text
    assert response.json()["accepted"] == 1


async def last_value(world: BudgetWorld, budget: BudgetRef) -> Decimal | None:
    state = await state_of(world, budget)
    assert state is not None
    return state["last_value"]


async def test_create_metadata_and_trace_name_budgets(budget_world: BudgetWorld) -> None:
    world = budget_world
    metadata = await create_budget(
        world, scope="metadata", scope_id="customer_id=acme", name="Acme"
    )
    trace_name = await create_budget(world, scope="trace_name", scope_id="answer", name="Answers")

    assert await rule_filters(world, metadata) == {
        "metadata_key": "customer_id",
        "metadata_value": "acme",
    }
    assert await rule_filters(world, trace_name) == {"trace_name": "answer"}
    response = await world.workspace.owner.get(f"{world.project_url}/budgets")
    assert response.status_code == 200, response.text
    assert sorted((row["scope"], row["scope_id"]) for row in response.json()) == [
        ("metadata", "customer_id=acme"),
        ("trace_name", "answer"),
    ]


async def test_scope_id_parsing_and_unknown_scope(budget_world: BudgetWorld) -> None:
    world = budget_world
    budget = await create_budget(world, scope="metadata", scope_id="customer_id=acme=prod")
    assert await rule_filters(world, budget) == {
        "metadata_key": "customer_id",
        "metadata_value": "acme=prod",  # split at the first `=` only
    }

    for scope_id in ("Customer=x", "customer_id", "customer_id=", "=acme", " customer_id=x"):
        response = await post_budget(world, "metadata", scope_id)
        assert response.status_code == 422, (scope_id, response.text)
        problem = response.json()
        assert problem["code"] == "UNKNOWN_SCOPE", scope_id
        assert problem["errors"] == [
            {
                "field": "scope_id",
                "message": "A metadata budget's scope is `key=value` with a valid key",
            }
        ]


async def test_metadata_scope_on_a_redacted_value_is_refused(budget_world: BudgetWorld) -> None:
    # Ingest stores this value as `[REDACTED]`, so a budget on it could never match a trace.
    world = budget_world
    secret = "token=sk-abcdefghijklmnopqrstuvwxyz"
    expected = [
        {
            "field": "scope_id",
            "message": "This value is redacted at ingest, so a budget on it can never match",
        }
    ]

    created = await post_budget(world, "metadata", secret)
    assert created.status_code == 422, created.text
    assert (created.json()["code"], created.json()["errors"]) == ("UNKNOWN_SCOPE", expected)

    budget = await create_budget(world, scope="metadata", scope_id="customer_id=acme")
    edited = await world.workspace.owner.patch(
        f"{world.project_url}/budgets/{budget.id}", json={"scope": "metadata", "scope_id": secret}
    )
    assert edited.status_code == 422, edited.text
    assert (edited.json()["code"], edited.json()["errors"]) == ("UNKNOWN_SCOPE", expected)


async def test_trace_name_scope_is_trimmed(budget_world: BudgetWorld) -> None:
    world = budget_world
    response = await post_budget(world, "trace_name", "  answer \t")
    assert response.status_code == 201, response.text
    assert response.json()["scope_id"] == "answer"  # as the gateway header is trimmed

    blank = await post_budget(world, "trace_name", "   ")
    assert blank.status_code == 422, blank.text
    assert blank.json()["code"] == "UNKNOWN_SCOPE"


async def test_trace_name_scope_is_at_most_256_characters(budget_world: BudgetWorld) -> None:
    world = budget_world
    longest = await post_budget(world, "trace_name", f" {VALUE_MAX} ")  # 256 once trimmed
    assert longest.status_code == 201, longest.text
    assert longest.json()["scope_id"] == VALUE_MAX

    too_long = await post_budget(world, "trace_name", f"{VALUE_MAX}v")
    assert too_long.status_code == 422, too_long.text
    assert too_long.json()["code"] == "UNKNOWN_SCOPE"


async def test_321_character_scope_id_is_accepted(budget_world: BudgetWorld) -> None:
    world = budget_world
    response = await post_budget(world, "metadata", LONGEST_SCOPE_ID)
    assert response.status_code == 201, response.text
    assert response.json()["scope_id"] == LONGEST_SCOPE_ID

    too_long = await post_budget(world, "metadata", f"{KEY_MAX}={VALUE_MAX}v")
    assert too_long.status_code == 422, too_long.text
    assert too_long.json()["code"] == "UNKNOWN_SCOPE"


async def test_spend_is_measured_over_raw_rows_by_containment(budget_world: BudgetWorld) -> None:
    world = budget_world
    by_pair = await create_budget(
        world, scope="metadata", scope_id="customer_id=acme", amount=Decimal(1), name="Acme"
    )
    by_name = await create_budget(
        world, scope="trace_name", scope_id="answer", amount=Decimal(1), name="Answers"
    )
    # The matching trace carries other keys too: containment, not equality of the object.
    await ingest_trace(world, name="answer", metadata={"customer_id": "acme", "plan": "free"})
    await ingest_trace(world, name="summarize", metadata={"customer_id": "globex"})
    await ingest_trace(world, name="summarize", metadata={"customer_id": "acme-2"})

    await evaluate(world, next_pass(world.clock))

    assert await last_value(world, by_pair) == STEP_COST
    assert await last_value(world, by_name) == STEP_COST


async def test_firing_and_resolving_on_a_metadata_scope(budget_world: BudgetWorld) -> None:
    world = budget_world
    rollover = period_start(BudgetPeriod.DAILY, world.clock.wall()) + timedelta(days=1)
    fired_at = rollover - timedelta(minutes=10)
    after = rollover + timedelta(minutes=1)
    budget = await create_budget(world, scope="metadata", scope_id="customer_id=acme")
    await ingest_trace(
        world,
        name="answer",
        metadata={"customer_id": "acme"},
        start=rollover - timedelta(minutes=20),
    )

    await evaluate(world, fired_at)
    state = await state_of(world, budget)
    assert state is not None
    assert (state["state"], state["since"], state["last_value"]) == (
        "firing",
        fired_at,
        STEP_COST,
    )
    assert await outbox_events(world) == ["budget.exceeded"]

    await evaluate(world, after)  # the next day: nothing in this period
    state = await state_of(world, budget)
    assert state is not None
    assert (state["state"], state["since"]) == ("ok", after)
    [event] = await events_of(world, budget)
    assert (event["event"], event["resolved_at"]) == ("budget.exceeded", after)
    assert await outbox_events(world) == ["budget.exceeded", "alert.resolved"]


def test_forces_raw_for_both_filters() -> None:
    assert MetricFilters(metadata=("customer_id", "acme")).forces_raw
    assert MetricFilters(trace_name="answer").forces_raw
    assert not MetricFilters(environment="prod", model="gpt-4.1").forces_raw

    stored = {"metadata_key": "a.b-c", "metadata_value": "x=y"}
    assert metric_filters(stored) == MetricFilters(metadata=("a.b-c", "x=y"))
    assert metric_filters({"trace_name": "answer"}) == MetricFilters(trace_name="answer")
    # Half a pair is not a filter: the rule would read every trace, never a wrong subset.
    assert metric_filters({"metadata_key": "customer_id"}) == MetricFilters()


async def test_feature_counters_on_create(budget_world: BudgetWorld) -> None:
    world = budget_world
    by_pair = metric_delta(FEATURE_USED, feature="budget_metadata_scope")
    by_name = metric_delta(FEATURE_USED, feature="budget_trace_name_scope")

    await create_budget(world, scope="metadata", scope_id="customer_id=acme", name="Acme")
    assert (by_pair.value, by_name.value) == (1, 0)

    await create_budget(world, scope="trace_name", scope_id="answer", name="Answers")
    assert (by_pair.value, by_name.value) == (1, 1)

    await create_budget(world, name="Everything")  # a project budget counts neither
    rejected = await post_budget(world, "metadata", "Customer=x")
    assert rejected.status_code == 422
    assert (by_pair.value, by_name.value) == (1, 1)
