"""Helpers shared by the budget tests.

A `BudgetWorld` is the gateway workspace (`tests/gateway_fixtures.py`) plus what budgets need: a
native API key to ingest spend with, an alert channel, so every alert event queues exactly
one outbox row, and the app's pinned clock (`tests/budgets/clock.py`). Budgets are made and
edited through the API; the alert tables are read past row-level security, like the worker
reads them.

Spend comes in steps of exactly $0.002: an ingested span with 5,000 input tokens of
`gpt-4.1-mini`, or a gateway answer with 5,000 prompt tokens, at the seed rate of $0.40 per
million input tokens.
"""

import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alerts.jobs import EvaluationRun, evaluate_all
from app.budgets.guard import StateBudgetGuard
from app.config import Settings
from app.db.rls import bind_project, bypass_rls
from app.gateway.budget import BudgetDecision
from tests.budgets.clock import next_pass
from tests.gateway_fixtures import GatewayWorkspace
from tests.gateway_support import FakeProvider, ManualClock, drain_recorder, read_gateway_spans
from tests.helpers import new_trace_id, span

Sessions = async_sessionmaker[AsyncSession]

PRICED_MODEL = "gpt-4.1-mini"
STEP_COST = Decimal("0.002")
STEP_TOKENS = 5000
CAP = Decimal("0.001")
BUDGET_NAME = "Daily cap"
CHAT_BODY = {"model": PRICED_MODEL, "messages": [{"role": "user", "content": "Hello"}]}
MESSAGE_BODY = {
    "model": "claude-haiku-4-5",
    "max_tokens": 64,
    "messages": [{"role": "user", "content": "Hello"}],
}


@dataclass
class BudgetWorld:
    workspace: GatewayWorkspace
    ingest: httpx.AsyncClient  # posts to `/v1/traces` with the project's native API key
    channel_id: uuid.UUID
    sessions: Sessions
    settings: Settings
    fake: FakeProvider
    clock: ManualClock  # the app's wall clock

    @property
    def project_url(self) -> str:
        return f"/api/v1/projects/{self.workspace.project_id}"


@dataclass(frozen=True)
class BudgetRef:
    id: uuid.UUID
    rule_id: uuid.UUID


async def create_budget(
    world: BudgetWorld,
    *,
    period: str = "daily",
    amount: Decimal = CAP,
    scope: str = "project",
    scope_id: str | None = None,
    name: str = BUDGET_NAME,
) -> BudgetRef:
    """A `block` budget (project-wide by default) that notifies the world's channel."""
    body = {
        "name": name,
        "scope": scope,
        "scope_id": scope_id,
        "period": period,
        "amount_usd": str(amount),
        "action": "block",
        "channel_ids": [str(world.channel_id)],
    }
    response = await world.workspace.owner.post(f"{world.project_url}/budgets", json=body)
    assert response.status_code == 201, response.text
    budget_id = uuid.UUID(response.json()["id"])
    async with world.sessions() as db:
        await bypass_rls(db)
        rule_id = await db.scalar(
            text("SELECT rule_id FROM budgets WHERE id = :id"), {"id": budget_id}
        )
    assert rule_id is not None
    return BudgetRef(budget_id, rule_id)


async def ingest_spend(
    world: BudgetWorld,
    steps: int = 1,
    *,
    start: datetime | None = None,
    model: str = PRICED_MODEL,
    user: str | None = None,
    tokens: int = STEP_TOKENS,
) -> None:
    """Ingest `steps` spans of `tokens` input tokens each (5,000 by default: $0.002 of
    `PRICED_MODEL`), of `model`, for the end user `user`, started at `start` (default: a second
    before the app's clock)."""
    started = start or world.clock.wall() - timedelta(seconds=1)
    usage = {"input_tokens": tokens, "output_tokens": 0, "cached_tokens": 0}
    trace = {"name": "answer", "environment": "prod", "user_id": user}
    spans = [
        span(trace_id=new_trace_id(), start=started, model=model, usage=usage, trace=trace)
        for _ in range(steps)
    ]
    response = await world.ingest.post("/v1/traces", json={"spans": spans})
    assert response.status_code == 200, response.text
    assert response.json()["accepted"] == steps


async def evaluate(world: BudgetWorld, now: datetime) -> EvaluationRun:
    """One evaluation pass at `now`, once every gateway span is stored. No rule may fail."""
    await drain_recorder()
    run = await evaluate_all(world.sessions, world.settings, now=now)
    assert (run.errors, run.projects_failed) == (0, 0)
    return run


async def firing_budget(world: BudgetWorld) -> tuple[BudgetRef, datetime]:
    """A $0.001 daily block budget with $0.002 spent, firing since the returned pass time."""
    budget = await create_budget(world)
    await ingest_spend(world)
    fired_at = next_pass(world.clock)
    await evaluate(world, fired_at)
    state = await state_of(world, budget)
    assert state is not None
    assert (state["state"], state["since"]) == ("firing", fired_at)
    return budget, fired_at


async def state_of(world: BudgetWorld, budget: BudgetRef) -> dict[str, Any] | None:
    """The budget rule's `alert_states` row, or None when there is none."""
    async with world.sessions() as db:
        await bypass_rls(db)
        row = await db.execute(
            text("SELECT * FROM alert_states WHERE rule_id = :id"), {"id": budget.rule_id}
        )
        found = row.mappings().first()
    return None if found is None else dict(found)


async def events_of(world: BudgetWorld, budget: BudgetRef) -> list[dict[str, Any]]:
    """The budget rule's alert events, oldest first: state, resolved_at and payload event."""
    async with world.sessions() as db:
        await bypass_rls(db)
        rows = await db.execute(
            text(
                "SELECT state, resolved_at, payload->>'event' AS event FROM alert_events "
                "WHERE rule_id = :id ORDER BY started_at, id"
            ),
            {"id": budget.rule_id},
        )
        return [dict(row) for row in rows.mappings()]


async def outbox_events(world: BudgetWorld) -> list[str]:
    """The event of every queued notification, oldest first; all go to the world's channel."""
    async with world.sessions() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT channel_id, payload->>'event' FROM notification_outbox "
                    "ORDER BY created_at, id"
                )
            )
        ).all()
    assert all(channel_id == world.channel_id for channel_id, _ in rows)
    return [event for _, event in rows]


async def count_rows(world: BudgetWorld, table: str, budget: BudgetRef) -> int:
    """Rows of `budgets`, `alert_rules`, `alert_states` or `alert_events` left for the budget."""
    assert table in {"budgets", "alert_rules", "alert_states", "alert_events"}, table
    column = "id" if table in {"budgets", "alert_rules"} else "rule_id"
    value = budget.id if table == "budgets" else budget.rule_id
    async with world.sessions() as db:
        await bypass_rls(db)
        count = await db.scalar(
            text(f"SELECT count(*) FROM {table} WHERE {column} = :value"),
            {"value": value},
        )
    return int(count or 0)


async def guard_decision(world: BudgetWorld, now: datetime) -> BudgetDecision:
    """What the gateway's guard decides at `now` for a project-wide call to the priced model."""
    project_id = world.workspace.project_id
    async with world.sessions() as db, db.begin():
        await bind_project(db, project_id)
        return await StateBudgetGuard().check(db, project_id, None, PRICED_MODEL, None, now)


def chat_completion(prompt_tokens: int) -> dict[str, Any]:
    """An OpenAI chat completion of `PRICED_MODEL` that used `prompt_tokens` input tokens."""
    return {
        "id": f"chatcmpl-{secrets.token_hex(8)}",
        "object": "chat.completion",
        "created": 1760000000,
        "model": PRICED_MODEL,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "Hi"},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": 0,
            "total_tokens": prompt_tokens,
        },
    }


async def chat(world: BudgetWorld, *, scripted_tokens: int | None = None) -> httpx.Response:
    """One chat completion through the gateway. With `scripted_tokens` the fake provider is
    first scripted to answer with that many prompt tokens; without, nothing is scripted, so a
    call that reaches the provider fails the test."""
    if scripted_tokens is not None:
        world.fake.reply_json(chat_completion(scripted_tokens))
    return await world.workspace.gw.post("/gw/v1/chat/completions", json=CHAT_BODY)


async def message(world: BudgetWorld) -> httpx.Response:
    """One Anthropic message through the gateway; the fake provider is not scripted."""
    return await world.workspace.gw.post("/gw/v1/messages", json=MESSAGE_BODY)


async def blocked_spans(world: BudgetWorld) -> list[dict[str, Any]]:
    """The project's gateway spans marked as blocked by a budget."""
    spans = await read_gateway_spans(world.sessions, world.workspace.project_id)
    return [row for row in spans if row["attributes"].get("spanlight.budget.blocked") is True]
