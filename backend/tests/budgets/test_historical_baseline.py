"""Historical gateway spend and newly settled calls consume one shared daily allowance."""

import asyncio
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.budgets.jobs import prune_budget_counters
from app.budgets.types import BudgetScope
from app.db.rls import bind_project
from tests.budgets.clock import next_pass
from tests.budgets.support import (
    CHAT_BODY,
    PRICED_MODEL,
    BudgetWorld,
    chat,
    chat_completion,
    create_budget,
    evaluate,
    ingest_spend,
)
from tests.budgets.test_block_after_evaluation import assert_blocked
from tests.budgets.test_deleted_spend import committed, ledger_rows, marker, sync_clock
from tests.gateway_support import Stall, drain_recorder
from tests.helpers import create_project


@pytest.mark.parametrize("evaluated", [False, True])
@pytest.mark.parametrize("edited_scope", [False, True])
async def test_historical_gateway_spend_leaves_only_twenty_cents(
    budget_world: BudgetWorld, evaluated: bool, edited_scope: bool
) -> None:
    world = budget_world
    # The scripted MockTransport reports $0.80 at the seeded $0.40 / million rate.
    assert (await chat(world, scripted_tokens=2_000_000)).status_code == 200
    await drain_recorder()
    budget = await create_budget(
        world,
        amount=Decimal("1"),
        scope="model" if edited_scope else "project",
        scope_id="unrelated-model" if edited_scope else None,
    )
    if edited_scope:
        response = await world.workspace.owner.patch(
            f"{world.project_url}/budgets/{budget.id}",
            json={"scope": "model", "scope_id": PRICED_MODEL},
        )
        assert response.status_code == 200, response.text
    if evaluated:
        await evaluate(world, next_pass(world.clock))

    assert (await chat(world, scripted_tokens=250_000)).status_code == 200
    await drain_recorder()
    assert (await chat(world, scripted_tokens=250_000)).status_code == 200
    await drain_recorder()

    # Script a response even for the forbidden call so a regression fails on admission,
    # rather than adding an unrelated unscripted-provider teardown error.
    response = await chat(world, scripted_tokens=250_000)
    assert (response.status_code, len(world.fake.requests)) == (402, 3)
    assert_blocked(response)


async def test_scope_edit_preserves_an_inflight_charge(budget_world: BudgetWorld) -> None:
    world = budget_world
    budget = await create_budget(world, amount=Decimal("1"))
    gate = asyncio.Event()
    world.fake.reply_json(chat_completion(250_000), before_headers=Stall(gate))
    call = asyncio.create_task(world.workspace.gw.post("/gw/v1/chat/completions", json=CHAT_BODY))
    try:
        async with asyncio.timeout(5):
            while not world.fake.requests:
                await asyncio.wait({call}, timeout=0.01)
        response = await world.workspace.owner.patch(
            f"{world.project_url}/budgets/{budget.id}",
            json={"scope": "model", "scope_id": PRICED_MODEL},
        )
    finally:
        gate.set()
        completed = await call
        await drain_recorder()
    assert completed.status_code == 200
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "BUDGET_RESERVATIONS_PENDING"


async def test_repeated_stale_evaluation_does_not_hide_or_duplicate_spend(
    budget_world: BudgetWorld,
) -> None:
    world = budget_world
    assert (await chat(world, scripted_tokens=2_000_000)).status_code == 200
    await drain_recorder()
    await create_budget(world, amount=Decimal("1"))
    await evaluate(world, next_pass(world.clock))
    assert (await chat(world, scripted_tokens=250_000)).status_code == 200
    await drain_recorder()
    await evaluate(world, next_pass(world.clock))
    assert (await chat(world, scripted_tokens=250_000)).status_code == 200
    await drain_recorder()
    assert (await chat(world, scripted_tokens=250_000)).status_code == 402
    assert len(world.fake.requests) == 3


async def test_call_admitted_before_budget_creation_is_in_late_history(
    budget_world: BudgetWorld,
) -> None:
    world = budget_world
    gate = asyncio.Event()
    world.fake.reply_json(chat_completion(2_000_000), before_headers=Stall(gate))
    call = asyncio.create_task(world.workspace.gw.post("/gw/v1/chat/completions", json=CHAT_BODY))
    try:
        async with asyncio.timeout(5):
            while not world.fake.requests:
                await asyncio.wait({call}, timeout=0.01)
        await create_budget(world, amount=Decimal("1"))
    finally:
        gate.set()
        completed = await call
        await drain_recorder()
    assert completed.status_code == 200
    assert (await chat(world, scripted_tokens=250_000)).status_code == 200
    await drain_recorder()
    assert (await chat(world, scripted_tokens=250_000)).status_code == 200
    await drain_recorder()
    assert (await chat(world, scripted_tokens=250_000)).status_code == 402
    assert len(world.fake.requests) == 3


async def test_previous_day_history_does_not_consume_new_daily_allowance(
    budget_world: BudgetWorld,
) -> None:
    world = budget_world
    assert (await chat(world, scripted_tokens=2_000_000)).status_code == 200
    await drain_recorder()
    await create_budget(world, amount=Decimal("1"))
    world.clock.advance(24 * 60 * 60)
    assert (await chat(world, scripted_tokens=2_000_000)).status_code == 200
    await drain_recorder()
    assert (await chat(world, scripted_tokens=250_000)).status_code == 200
    await drain_recorder()
    assert (await chat(world, scripted_tokens=250_000)).status_code == 200
    await drain_recorder()
    assert (await chat(world, scripted_tokens=250_000)).status_code == 402
    assert len(world.fake.requests) == 4


async def test_monthly_hard_budget_requires_31_days_and_rechecks_retention(
    budget_world: BudgetWorld,
) -> None:
    world = budget_world
    await create_budget(world, period="monthly", amount=Decimal("1"))
    response = await world.workspace.owner.patch(world.project_url, json={"retention_days": 30})
    assert response.status_code == 200, response.text
    response = await chat(world, scripted_tokens=0)
    assert response.status_code == 402
    assert "at least 31 days" in response.json()["error"]["message"]
    response = await world.workspace.owner.post(
        f"{world.project_url}/budgets",
        json={
            "name": "unsupported month",
            "scope": "project",
            "period": "monthly",
            "amount_usd": "1",
            "action": "block",
            "channel_ids": [],
        },
    )
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "BUDGET_HISTORY_RETENTION"
    assert world.fake.requests == []


async def test_cost_correction_of_correlated_span_is_counted_once(
    budget_world: BudgetWorld,
) -> None:
    world = budget_world
    await create_budget(world, amount=Decimal("1"))
    assert (await chat(world, scripted_tokens=250_000)).status_code == 200
    await drain_recorder()
    # The same stored identity can be corrected by ingestion. The fresh overlap read must
    # replace its old $0.10 charge rather than adding both prices or keeping the old price.
    async with world.sessions() as db, db.begin():
        await bind_project(db, world.workspace.project_id)
        await db.execute(
            text("UPDATE spans SET cost_usd = 0.8 WHERE project_id = :id"),
            {"id": world.workspace.project_id},
        )
    assert (await chat(world, scripted_tokens=250_000)).status_code == 200
    await drain_recorder()
    assert (await chat(world, scripted_tokens=250_000)).status_code == 200
    await drain_recorder()
    assert (await chat(world, scripted_tokens=250_000)).status_code == 402
    assert len(world.fake.requests) == 3


async def test_scope_edit_cannot_drop_a_settled_unknown_charge(budget_world: BudgetWorld) -> None:
    world = budget_world
    budget = await create_budget(world, amount=Decimal("1"))
    answer = chat_completion(0)
    del answer["usage"]
    world.fake.reply_json(answer)
    assert (
        await world.workspace.gw.post("/gw/v1/chat/completions", json=CHAT_BODY)
    ).status_code == 200
    await drain_recorder()
    response = await world.workspace.owner.patch(
        f"{world.project_url}/budgets/{budget.id}",
        json={"scope": "model", "scope_id": PRICED_MODEL},
    )
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "BUDGET_RESERVATIONS_PENDING"
    world.clock.advance(24 * 60 * 60)
    response = await world.workspace.owner.patch(
        f"{world.project_url}/budgets/{budget.id}",
        json={"scope": "model", "scope_id": PRICED_MODEL},
    )
    assert response.status_code == 200, response.text


async def test_deletion_coverage_respects_rls_and_project_cascade(
    budget_world: BudgetWorld,
) -> None:
    world = budget_world
    sync_clock(world)
    await ingest_spend(world, tokens=250_000)
    other_id = uuid.UUID(
        await create_project(world.workspace.owner, str(world.workspace.org_id), "other")
    )
    async with world.sessions() as db, db.begin():
        await bind_project(db, other_id)
        deleted = await db.execute(
            text("DELETE FROM spans WHERE project_id = :id"), {"id": world.workspace.project_id}
        )
        assert deleted.rowcount == 0
    assert (await ledger_rows(world), await ledger_rows(world, other_id)) == (0, 0)
    async with world.sessions() as db, db.begin():
        await bind_project(db, world.workspace.project_id)
        await db.execute(
            text("DELETE FROM spans WHERE project_id = :id"), {"id": world.workspace.project_id}
        )
    assert (await ledger_rows(world), await ledger_rows(world, other_id)) == (1, 0)
    async with world.sessions() as db:
        markers = (
            await db.execute(
                text(
                    "SELECT budget_history_missing_through FROM projects "
                    "WHERE id IN (:first, :second)"
                ),
                {"first": world.workspace.project_id, "second": other_id},
            )
        ).scalars()
        assert markers.all() == [None, None]
    # Actual cascading project deletion must neither deadlock nor recreate a project row.
    await ingest_spend(world, tokens=250_000)
    project = await world.workspace.owner.get(world.project_url)
    response = await world.workspace.owner.delete(
        world.project_url,
        json={"confirm": project.json()["slug"], "password": "correct horse battery"},
    )
    assert response.status_code == 204, response.text
    async with world.sessions() as db:
        count = await db.scalar(
            text("SELECT count(*) FROM projects WHERE id = :id"), {"id": world.workspace.project_id}
        )
        assert count == 0


async def test_correlated_call_keeps_its_admission_period_after_timestamp_correction(
    budget_world: BudgetWorld,
) -> None:
    world = budget_world
    await create_budget(world, amount=Decimal("1"))
    assert (await chat(world, scripted_tokens=2_000_000)).status_code == 200
    await drain_recorder()
    world.clock.advance(24 * 60 * 60)
    async with world.sessions() as db, db.begin():
        await bind_project(db, world.workspace.project_id)
        await db.execute(
            text("UPDATE spans SET started_at = :now, ended_at = :now WHERE project_id = :id"),
            {"id": world.workspace.project_id, "now": world.clock.wall()},
        )
    assert (await chat(world, scripted_tokens=2_000_000)).status_code == 200
    await drain_recorder()
    assert (await chat(world, scripted_tokens=250_000)).status_code == 200
    await drain_recorder()
    assert (await chat(world, scripted_tokens=250_000)).status_code == 200
    await drain_recorder()
    assert (await chat(world, scripted_tokens=250_000)).status_code == 402
    assert len(world.fake.requests) == 4


@pytest.mark.parametrize("correct_before_edit", [False, True])
@pytest.mark.parametrize("days", [-1, 1])
async def test_scope_edit_preserves_admission_day_before_and_after_correction(
    budget_world: BudgetWorld,
    days: int,
    correct_before_edit: bool,
) -> None:
    world = budget_world
    budget = await create_budget(world, amount=Decimal("1"))
    assert (await chat(world, scripted_tokens=2_000_000)).status_code == 200
    await drain_recorder()
    if correct_before_edit:
        async with world.sessions() as db, db.begin():
            await bind_project(db, world.workspace.project_id)
            await db.execute(
                text(
                    "UPDATE spans SET started_at = started_at + make_interval(days => :days), "
                    "ended_at = ended_at + make_interval(days => :days) WHERE project_id = :id"
                ),
                {"days": days, "id": world.workspace.project_id},
            )
    response = await world.workspace.owner.patch(
        f"{world.project_url}/budgets/{budget.id}",
        json={"scope": "model", "scope_id": PRICED_MODEL},
    )
    assert response.status_code == 200, response.text
    if not correct_before_edit:
        async with world.sessions() as db, db.begin():
            await bind_project(db, world.workspace.project_id)
            await db.execute(
                text(
                    "UPDATE spans SET started_at = started_at + make_interval(days => :days), "
                    "ended_at = ended_at + make_interval(days => :days) WHERE project_id = :id"
                ),
                {"days": days, "id": world.workspace.project_id},
            )
    assert (await chat(world, scripted_tokens=250_000)).status_code == 200
    await drain_recorder()
    assert (await chat(world, scripted_tokens=250_000)).status_code == 200
    await drain_recorder()
    assert (await chat(world, scripted_tokens=250_000)).status_code == 402


@pytest.mark.parametrize("age_days", [70, 120])
@pytest.mark.parametrize("period", ["daily", "monthly"])
async def test_pruning_keeps_identity_while_corrected_raw_span_remains(
    budget_world: BudgetWorld,
    age_days: int,
    period: str,
) -> None:
    world = budget_world
    response = await world.workspace.owner.patch(world.project_url, json={"retention_days": 90})
    assert response.status_code == 200
    await create_budget(world, amount=Decimal("1"), period=period)
    assert (await chat(world, scripted_tokens=2_000_000)).status_code == 200
    await drain_recorder()
    world.clock.advance(age_days * 24 * 60 * 60)
    async with world.sessions() as db, db.begin():
        await bind_project(db, world.workspace.project_id)
        await db.execute(
            text("UPDATE spans SET started_at = :now, ended_at = :now WHERE project_id = :id"),
            {"id": world.workspace.project_id, "now": world.clock.wall()},
        )
    await prune_budget_counters(world.sessions, now=world.clock.wall())
    assert (await chat(world, scripted_tokens=2_000_000)).status_code == 200
    await drain_recorder()
    assert (await chat(world, scripted_tokens=250_000)).status_code == 200
    await drain_recorder()
    assert (await chat(world, scripted_tokens=250_000)).status_code == 200
    await drain_recorder()
    assert (await chat(world, scripted_tokens=250_000)).status_code == 402


async def test_safe_scope_edit_retains_available_allowance(budget_world: BudgetWorld) -> None:
    world = budget_world
    budget = await create_budget(world, amount=Decimal("1"))
    assert (await chat(world, scripted_tokens=2_000_000)).status_code == 200
    await drain_recorder()
    response = await world.workspace.owner.patch(
        f"{world.project_url}/budgets/{budget.id}",
        json={"scope": "model", "scope_id": PRICED_MODEL},
    )
    assert response.status_code == 200, response.text
    assert (await chat(world, scripted_tokens=250_000)).status_code == 200
    await drain_recorder()
    assert (await chat(world, scripted_tokens=250_000)).status_code == 200
    await drain_recorder()
    assert (await chat(world, scripted_tokens=250_000)).status_code == 402


@pytest.mark.parametrize("period", ["daily", "monthly"])
async def test_absent_span_correlation_lives_90_days_from_admission(
    budget_world: BudgetWorld,
    period: str,
) -> None:
    world = budget_world
    budget = await create_budget(world, amount=Decimal("1"), period=period)
    assert (await chat(world, scripted_tokens=250_000)).status_code == 200
    await drain_recorder()
    async with world.sessions() as db, db.begin():
        await bind_project(db, world.workspace.project_id)
        await db.execute(
            text("DELETE FROM traces WHERE project_id = :id"), {"id": world.workspace.project_id}
        )
    world.clock.advance(89 * 24 * 60 * 60)
    await prune_budget_counters(world.sessions, now=world.clock.wall())
    async with world.sessions() as db:
        await bind_project(db, world.workspace.project_id)
        remaining = await db.scalar(
            text("SELECT count(*) FROM budget_reservations WHERE budget_id = :id"),
            {"id": budget.id},
        )
        counters = await db.scalar(
            text("SELECT count(*) FROM budget_counters WHERE budget_id = :id"), {"id": budget.id}
        )
        assert (remaining, counters) == (1, 0)
    world.clock.advance(2 * 24 * 60 * 60)
    await prune_budget_counters(world.sessions, now=world.clock.wall())
    async with world.sessions() as db:
        await bind_project(db, world.workspace.project_id)
        remaining = await db.scalar(
            text("SELECT count(*) FROM budget_reservations WHERE budget_id = :id"),
            {"id": budget.id},
        )
        assert remaining == 0


async def test_reclassified_scope_follows_later_group_correction(budget_world: BudgetWorld) -> None:
    world = budget_world
    budget = await create_budget(world, amount=Decimal("1"))
    assert (await chat(world, scripted_tokens=2_000_000)).status_code == 200
    await drain_recorder()
    response = await world.workspace.owner.patch(
        f"{world.project_url}/budgets/{budget.id}",
        json={"scope": "model", "scope_id": PRICED_MODEL},
    )
    assert response.status_code == 200
    async with world.sessions() as db, db.begin():
        await bind_project(db, world.workspace.project_id)
        await db.execute(
            text("UPDATE spans SET model = 'corrected-model' WHERE project_id = :id"),
            {"id": world.workspace.project_id},
        )
    assert (await chat(world, scripted_tokens=250_000)).status_code == 200
    await drain_recorder()
    assert (await chat(world, scripted_tokens=250_000)).status_code == 200
    await drain_recorder()
    assert (await chat(world, scripted_tokens=250_000)).status_code == 200
    await drain_recorder()
    response = await world.workspace.owner.patch(
        f"{world.project_url}/budgets/{budget.id}",
        json={"scope": "project", "scope_id": None},
    )
    assert response.status_code == 200
    assert (await chat(world, scripted_tokens=250_000)).status_code == 402


async def test_repeated_period_edits_use_original_admission_clock(
    budget_world: BudgetWorld,
) -> None:
    world = budget_world
    budget = await create_budget(world, amount=Decimal("1"))
    assert (await chat(world, scripted_tokens=2_000_000)).status_code == 200
    await drain_recorder()
    async with world.sessions() as db, db.begin():
        await bind_project(db, world.workspace.project_id)
        await db.execute(
            text(
                "UPDATE spans SET started_at = started_at - interval '1 day', "
                "ended_at = ended_at - interval '1 day' WHERE project_id = :id"
            ),
            {"id": world.workspace.project_id},
        )
    response = await world.workspace.owner.patch(
        f"{world.project_url}/budgets/{budget.id}",
        json={"period": "monthly"},
    )
    assert response.status_code == 200, response.text
    response = await world.workspace.owner.patch(
        f"{world.project_url}/budgets/{budget.id}",
        json={"period": "daily"},
    )
    assert response.status_code == 200, response.text
    assert (await chat(world, scripted_tokens=250_000)).status_code == 200
    await drain_recorder()
    assert (await chat(world, scripted_tokens=250_000)).status_code == 200
    await drain_recorder()
    assert (await chat(world, scripted_tokens=250_000)).status_code == 402


async def test_deleted_reclassified_history_keeps_its_retained_charge(
    budget_world: BudgetWorld,
) -> None:
    world = budget_world
    budget = await create_budget(world, amount=Decimal("1"))
    assert (await chat(world, scripted_tokens=250_000)).status_code == 200
    await drain_recorder()
    response = await world.workspace.owner.patch(
        f"{world.project_url}/budgets/{budget.id}",
        json={"scope": "model", "scope_id": PRICED_MODEL},
    )
    assert response.status_code == 200
    async with world.sessions() as db, db.begin():
        await bind_project(db, world.workspace.project_id)
        await db.execute(
            text(
                "UPDATE spans SET cost_usd = 0.8, started_at = started_at - interval '1 day', "
                "ended_at = ended_at - interval '1 day' WHERE project_id = :id"
            ),
            {"id": world.workspace.project_id},
        )
        await db.execute(
            text("DELETE FROM traces WHERE project_id = :id"), {"id": world.workspace.project_id}
        )
    assert await committed(world, budget, BudgetScope.MODEL, PRICED_MODEL) == Decimal("0.1")
    assert await marker(world) is None
