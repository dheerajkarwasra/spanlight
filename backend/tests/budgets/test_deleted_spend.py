"""Deleted current-month priced spans stay in `budget_deleted_spans` and keep counting.

The budget world's app clock is pinned to yesterday 12:00 UTC, and the ledger keeps only spans of
the database's current UTC month, so the tests that need a deleted span counted first move the
app clock to now (`sync_clock`): on the first of a month, yesterday is last month.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import text

from app.budgets.history import read_history
from app.budgets.periods import next_reset, period_start
from app.budgets.types import BudgetPeriod, BudgetScope
from app.db.rls import bind_project, bypass_rls
from app.end_users.erasure_job import erase_end_user
from tests.budgets.support import (
    CHAT_BODY,
    PRICED_MODEL,
    BudgetRef,
    BudgetWorld,
    chat,
    chat_completion,
    create_budget,
    ingest_spend,
)
from tests.gateway_support import drain_recorder
from tests.helpers import new_trace_id, span
from tests.jobs.retention_support import apply_retention


def sync_clock(world: BudgetWorld) -> None:
    world.clock.advance((datetime.now(UTC) - world.clock.wall()).total_seconds())


async def delete_traces(world: BudgetWorld) -> None:
    """Delete the project's traces (and spans) as the app role, bound to the project."""
    async with world.sessions() as db, db.begin():
        await bind_project(db, world.workspace.project_id)
        await db.execute(
            text("DELETE FROM traces WHERE project_id = :id"), {"id": world.workspace.project_id}
        )


async def ledger_rows(world: BudgetWorld, project_id: uuid.UUID | None = None) -> int:
    async with world.sessions() as db:
        await bypass_rls(db)
        count = await db.scalar(
            text("SELECT count(*) FROM budget_deleted_spans WHERE project_id = :id"),
            {"id": project_id or world.workspace.project_id},
        )
    return int(count or 0)


async def marker(world: BudgetWorld) -> datetime | None:
    async with world.sessions() as db:
        await bind_project(db, world.workspace.project_id)
        return await db.scalar(
            text("SELECT budget_history_missing_through FROM projects WHERE id = :id"),
            {"id": world.workspace.project_id},
        )


async def committed(
    world: BudgetWorld,
    budget: BudgetRef,
    scope: BudgetScope = BudgetScope.PROJECT,
    scope_id: str | None = None,
) -> Decimal:
    """What admission would count for a daily `budget` now; the counter row the guard would
    have made is made here."""
    project_id, now = world.workspace.project_id, world.clock.wall()
    started = period_start(BudgetPeriod.DAILY, now)
    async with world.sessions() as db, db.begin():
        await bind_project(db, project_id)
        await db.execute(
            text(
                "INSERT INTO budget_counters (budget_id, period_start, project_id) "
                "VALUES (:budget_id, :started, :project_id) ON CONFLICT DO NOTHING"
            ),
            {"budget_id": budget.id, "started": started, "project_id": project_id},
        )
        history = await read_history(
            db,
            project_id,
            budget.id,
            scope,
            scope_id,
            started,
            next_reset(BudgetPeriod.DAILY, now),
            now=now,
        )
    return history.committed_usd


async def test_erasure_keeps_spend_and_admits(budget_world: BudgetWorld) -> None:
    world = budget_world
    sync_clock(world)
    response = await world.workspace.owner.patch(world.project_url, json={"retention_days": 90})
    assert response.status_code == 200, response.text
    await ingest_spend(world, tokens=1_500_000, user="u1")  # $0.60 at $0.40 per million
    await create_budget(world, period="monthly", amount=Decimal("1"))
    await erase_end_user(
        world.sessions, project_id=world.workspace.project_id, external_user_id="u1", counts={}
    )
    # Output tokens of PRICED_MODEL cost $1.60 per million: `max_tokens` sets the estimate.
    world.fake.reply_json(chat_completion(0))
    response = await world.workspace.gw.post(
        "/gw/v1/chat/completions", json={**CHAT_BODY, "max_tokens": 187_500}
    )
    assert response.status_code == 200, response.text  # $0.60 + $0.30
    await drain_recorder()
    response = await world.workspace.gw.post(
        "/gw/v1/chat/completions", json={**CHAT_BODY, "max_tokens": 312_500}
    )
    assert response.status_code == 402, response.text  # $0.60 + $0.50
    message = response.json()["error"]["message"]
    assert "would be exceeded" in message
    assert "history is missing" not in message
    assert len(world.fake.requests) == 1
    assert await marker(world) is None


async def test_correlated_span_deleted_before_settle_charges_once(
    budget_world: BudgetWorld,
) -> None:
    world = budget_world
    sync_clock(world)
    budget = await create_budget(world, amount=Decimal("1"))
    assert (await chat(world, scripted_tokens=250_000)).status_code == 200
    await drain_recorder()
    await delete_traces(world)
    assert await ledger_rows(world) == 1
    assert await committed(world, budget) == Decimal("0.1")


async def test_previous_month_span_never_enters_ledger(budget_world: BudgetWorld) -> None:
    world = budget_world
    month_start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    await ingest_spend(world, start=month_start - timedelta(minutes=1), tokens=250_000)
    await delete_traces(world)
    assert await ledger_rows(world) == 0
    await ingest_spend(world, start=month_start + timedelta(minutes=1), tokens=250_000)
    await delete_traces(world)
    assert await ledger_rows(world) == 1


async def test_replayed_deleted_span_counts_once(budget_world: BudgetWorld) -> None:
    world = budget_world
    sync_clock(world)
    budget = await create_budget(world, amount=Decimal("1"))
    usage = {"input_tokens": 1_000_000, "output_tokens": 0, "cached_tokens": 0}  # $0.40
    body = {
        "spans": [
            span(
                trace_id=new_trace_id(),
                start=world.clock.wall() - timedelta(seconds=1),
                model=PRICED_MODEL,
                usage=usage,
            )
        ]
    }
    assert (await world.ingest.post("/v1/traces", json=body)).status_code == 200
    await delete_traces(world)
    assert (await world.ingest.post("/v1/traces", json=body)).status_code == 200
    assert await ledger_rows(world) == 1
    assert await committed(world, budget) == Decimal("0.40")


async def test_model_scope_exact_and_user_scope_conservative(budget_world: BudgetWorld) -> None:
    world = budget_world
    sync_clock(world)
    await ingest_spend(world, model="gpt-4.1", tokens=250_000, user="u1")  # $0.50
    mini = await create_budget(world, scope="model", scope_id="gpt-4.1-mini", name="mini")
    exact = await create_budget(world, scope="model", scope_id="gpt-4.1", name="exact")
    other = await create_budget(world, scope="user", scope_id="someone-else", name="other")
    await delete_traces(world)
    assert await committed(world, mini, BudgetScope.MODEL, "gpt-4.1-mini") == 0
    assert await committed(world, exact, BudgetScope.MODEL, "gpt-4.1") == Decimal("0.5")
    assert await committed(world, other, BudgetScope.USER, "someone-else") == Decimal("0.5")


async def test_project_delete_cascade_writes_no_ledger_row(budget_world: BudgetWorld) -> None:
    world = budget_world
    sync_clock(world)
    await ingest_spend(world, tokens=250_000)
    project = await world.workspace.owner.get(world.project_url)
    response = await world.workspace.owner.delete(
        world.project_url,
        json={"confirm": project.json()["slug"], "password": "correct horse battery"},
    )
    assert response.status_code == 204, response.text
    assert await ledger_rows(world) == 0


async def test_retention_deletion_writes_the_ledger_under_worker_bypass(
    budget_world: BudgetWorld,
) -> None:
    world = budget_world
    sync_clock(world)
    response = await world.workspace.owner.patch(world.project_url, json={"retention_days": 1})
    assert response.status_code == 200, response.text
    await ingest_spend(world, tokens=250_000)
    # Two days on, the one-day retention covers the span; the task runs with the worker bypass.
    await apply_retention(world.sessions, now=world.clock.wall() + timedelta(days=2))
    async with world.sessions() as db:
        await bypass_rls(db)
        spans = await db.scalar(
            text("SELECT count(*) FROM spans WHERE project_id = :id"),
            {"id": world.workspace.project_id},
        )
    assert spans == 0
    assert await ledger_rows(world) == 1
