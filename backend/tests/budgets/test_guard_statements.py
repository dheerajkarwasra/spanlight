"""A steady-state admission uses one client statement, including pending delta folds.

The VOLATILE function keeps the counter lock and history read in separate SQL statements:
history must use a snapshot taken after the lock was granted (`app.budgets.history`).
Missing counters or stale hours still use the Python repair path.
"""

from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import event, text

from app.budgets.reserve import BlockBudget, reserve
from app.budgets.types import BudgetPeriod
from app.db.rls import bind_project
from app.pricing.cost import load_price_book
from tests.budgets.support import (
    CHAT_BODY,
    PRICED_MODEL,
    BudgetWorld,
    chat_completion,
    create_budget,
)
from tests.budgets.test_guard_dimensions import stored_trace
from tests.budgets.test_hard_block_concurrency import _counter
from tests.gateway_support import drain_recorder
from tests.helpers import new_span_id, new_trace_id

ADMISSION_TABLES = (
    "budget_counters",
    "budget_reservations",
    "budget_bucket_snapshots",
    "budget_bucket_deltas",
    "budget_bucket_versions",
)


async def test_steady_state_reserve_uses_exactly_one_client_statement(
    budget_world: BudgetWorld,
) -> None:
    world = budget_world
    budget = await create_budget(world, amount=Decimal("1"))
    block = BlockBudget(budget.id, "b", BudgetPeriod.DAILY, Decimal("1"))
    project_id, now = world.workspace.project_id, world.clock.wall()
    async with world.sessions() as db, db.begin():
        await bind_project(db, project_id)
        assert (
            await reserve(
                db,
                project_id,
                [block],
                Decimal("0.0001"),
                trace_id=new_trace_id(),
                span_id=new_span_id(),
                now=now,
            )
            is None
        )

    statements: list[str] = []

    def record(_conn: Any, _cursor: Any, statement: str, *_rest: Any) -> None:
        statements.append(statement)

    async with world.sessions() as db, db.begin():
        await bind_project(db, project_id)
        engine = world.sessions.kw["bind"].sync_engine
        event.listen(engine, "before_cursor_execute", record)
        try:
            refusal = await reserve(
                db,
                project_id,
                [block],
                Decimal("0.0001"),
                trace_id=new_trace_id(),
                span_id=new_span_id(),
                now=now,
            )
        finally:
            event.remove(engine, "before_cursor_execute", record)
    assert refusal is None
    assert len(statements) == 1, "\n".join(statements)


async def _guard_statements(
    world: BudgetWorld, gateway_app: Any, *, headers: dict[str, str] | None = None
) -> list[str]:
    """Capture every SQL statement in the committed guard transaction, including RLS binding.

    Group by transaction so key loading and the asynchronous recorder cannot pollute counts.
    BEGIN/COMMIT protocol messages are not cursor statements. Warm the existing seed book
    outside the measurement; no cache or TTL is changed.
    """
    await drain_recorder()
    async with world.sessions() as db:
        await load_price_book(db)
    pending: dict[Any, list[str]] = {}
    guards: list[list[str]] = []

    def record(conn: Any, _cursor: Any, statement: str, *_rest: Any) -> None:
        pending.setdefault(conn, []).append(" ".join(statement.split()))

    def commit(conn: Any) -> None:
        statements = pending.pop(conn, [])
        if any(
            ("FROM budgets" in sql or "JOIN budgets" in sql) and "FOR SHARE" in sql
            for sql in statements
        ):
            guards.append(statements)

    def rollback(conn: Any) -> None:
        pending.pop(conn, None)

    engine = gateway_app.state.engine.sync_engine
    event.listen(engine, "before_cursor_execute", record)
    event.listen(engine, "commit", commit)
    event.listen(engine, "rollback", rollback)
    world.fake.reply_json(chat_completion(100))
    try:
        response = await world.workspace.gw.post(
            "/gw/v1/chat/completions", json=CHAT_BODY, headers=headers
        )
    finally:
        event.remove(engine, "before_cursor_execute", record)
        event.remove(engine, "commit", commit)
        event.remove(engine, "rollback", rollback)
        await drain_recorder()
    assert response.status_code == 200, response.text
    assert len(guards) == 1, guards
    return guards[0]


async def _admission_statements(world: BudgetWorld, gateway_app: Any) -> list[str]:
    """The counter and reservation statements of one admitted call, in order; the span's
    settle (in the recorder) is drained afterwards and not counted.

    Include the hourly delta fold: it is admission work even when its SQL does not mention
    counters or reservations, which the old table filter silently missed.
    """
    statements = await _guard_statements(world, gateway_app)
    return [
        sql
        for sql in statements
        if "budget_reserve_fast" in sql or any(table in sql for table in ADMISSION_TABLES)
    ]


async def test_a_steady_state_admission_with_deltas_runs_one_statement(
    budget_world: BudgetWorld, gateway_app: Any
) -> None:
    world = budget_world
    budget = await create_budget(world, amount=Decimal("1"))

    first = await _admission_statements(world, gateway_app)
    assert any(s.startswith("INSERT INTO budget_counters") for s in first), "\n".join(first)

    second = await _admission_statements(world, gateway_app)
    # The function completes the counter lock before its history read folds pending deltas.
    assert len(second) == 1, "\n".join(second)
    assert "budget_reserve_fast" in second[0], "\n".join(second)

    assert await _counter(world, budget.id) == (
        Decimal("0.00008"),
        Decimal(0),
        ["settled", "settled"],
    )


async def test_a_keyed_call_without_budgets_runs_two_guard_statements(
    budget_world: BudgetWorld, gateway_app: Any
) -> None:
    world = budget_world
    trace_id = await stored_trace(world, name="answer", metadata={"team": "red"})

    statements = await _guard_statements(
        world, gateway_app, headers={"x-spanlight-trace-id": trace_id}
    )

    # Binding plus the fresh guard read; a joined dimension lookup is allowed.
    assert len(statements) == 2, "\n".join(statements)
    assert not any("INSERT INTO budget_reservations" in sql for sql in statements)


@pytest.mark.parametrize(
    ("scope", "scope_id", "limit"),
    [
        # Setting the trace's user bumps its hour; D2 adds one fast call before slow repair.
        pytest.param("project", None, 7, id="project"),
        pytest.param("model", PRICED_MODEL, 7, id="model"),
        pytest.param("gateway_key", None, 7, id="gateway-key"),
        pytest.param("user", "alice", 7, id="user"),
        pytest.param("trace_name", "answer", 7, id="trace-name"),
        pytest.param("metadata", "team=red", 7, id="metadata"),
    ],
)
async def test_a_keyed_call_with_a_budget_stays_within_its_guard_statement_limit(
    budget_world: BudgetWorld,
    gateway_app: Any,
    scope: str,
    scope_id: str | None,
    limit: int,
) -> None:
    world = budget_world
    if scope == "gateway_key":
        scope_id = str(world.workspace.gateway_key_id)
    budget = await create_budget(world, amount=Decimal("1"), scope=scope, scope_id=scope_id)
    trace_id = await stored_trace(world, name="answer", metadata={"team": "red"})
    headers = {"x-spanlight-trace-id": trace_id, "x-spanlight-user": "alice"}
    await _guard_statements(world, gateway_app, headers=headers)  # create counter and hour rows

    async with world.sessions() as db:
        await bind_project(db, world.workspace.project_id)
        pending = await db.scalar(
            text("SELECT count(*) FROM budget_bucket_deltas WHERE budget_id = :id"),
            {"id": budget.id},
        )
    assert pending is not None and pending > 0  # exercise a real settlement delta, not idle hours

    statements = await _guard_statements(world, gateway_app, headers=headers)

    assert await _counter(world, budget.id) == (
        Decimal("0.00008"),
        Decimal(0),
        ["settled", "settled"],
    )
    # Binding, joined guard/dimensions/overrides, the fast call (slow here: the first call
    # changed the trace), then the slow path's counter lock, history, recompute and hold.
    assert len(statements) <= limit, "\n".join(statements)
    assert any("budget_reserve_fast" in sql for sql in statements), "\n".join(statements)
    assert any(
        sql.startswith("SELECT budget_counters") and "FOR UPDATE" in sql for sql in statements
    ), "\n".join(statements)
    assert any("budgets" in sql and "price_overrides" in sql for sql in statements), (
        "Organization overrides must be read in the guard query, not cached or omitted.\n"
        + "\n".join(statements)
    )
    async with world.sessions() as db:
        await bind_project(db, world.workspace.project_id)
        folded, pending = (
            await db.execute(
                text(
                    "SELECT (SELECT sum(committed_usd) FROM budget_bucket_snapshots "
                    "WHERE budget_id = :id), "
                    "(SELECT sum(amount_usd) FROM budget_bucket_deltas WHERE budget_id = :id)"
                ),
                {"id": budget.id},
            )
        ).one()
    # The SDK span has no gateway key. Other scopes include its $0.002; every scope folds
    # the first call's $0.00004 and leaves only the second call's settlement delta pending.
    assert folded == (Decimal("0.00004") if scope == "gateway_key" else Decimal("0.00204"))
    assert pending == Decimal("0.00004")


async def test_a_fresh_free_override_is_used_within_two_guard_statements(
    budget_world: BudgetWorld, gateway_app: Any
) -> None:
    world = budget_world
    budget = await create_budget(world, amount=Decimal("1"))
    await _guard_statements(world, gateway_app)
    override = await world.workspace.owner.post(
        f"/api/v1/orgs/{world.workspace.org_id}/price-overrides",
        json={
            "provider": "openai",
            "model_pattern": PRICED_MODEL,
            "input_per_mtok": "0",
            "output_per_mtok": "0",
            "effective_from": "2025-01-01T00:00:00Z",
        },
    )
    assert override.status_code == 201, override.text

    statements = await _guard_statements(world, gateway_app)

    # The next call sees the override without a TTL wait, and adds no reservation or charge.
    assert await _counter(world, budget.id) == (Decimal("0.00004"), Decimal(0), ["settled"])
    assert len(statements) == 2, "\n".join(statements)


async def test_counter_lock_finishes_in_a_separate_statement_before_any_history_read(
    budget_world: BudgetWorld, gateway_app: Any
) -> None:
    world = budget_world
    await create_budget(world, amount=Decimal("1"))
    await _guard_statements(world, gateway_app)  # steady state: no missing counter rows

    calls = await _admission_statements(world, gateway_app)
    assert len(calls) == 1 and "budget_reserve_fast" in calls[0], "\n".join(calls)
    async with world.sessions() as db:
        source, volatility, language = (
            await db.execute(
                text(
                    "SELECT p.prosrc, p.provolatile, l.lanname FROM pg_proc p "
                    "JOIN pg_namespace n ON n.oid = p.pronamespace "
                    "JOIN pg_language l ON l.oid = p.prolang "
                    "WHERE n.nspname = 'public' AND p.proname = 'budget_reserve_fast'"
                )
            )
        ).one()
    assert (volatility, language) == ("v", "plpgsql")
    # Inspect server statements now that the client sees only the function call. The race in
    # test_snapshot_locks also checks this property through the admission's observed spend.
    statements = [" ".join(sql.split()) for sql in source.split(";")]

    locks = [
        index
        for index, sql in enumerate(statements)
        if "budget_counters" in sql and "FOR UPDATE" in sql
    ]
    history = [
        index
        for index, sql in enumerate(statements)
        if "budget_bucket_snapshots" in sql or "budget_bucket_deltas" in sql
    ]
    assert locks and history, "\n".join(statements)
    # A CTE cannot fix READ COMMITTED's statement snapshot: even a lock CTE evaluated first
    # shares the history read's pre-wait snapshot. Equality must fail, as must reversed order.
    assert locks[0] < min(history), "\n".join(statements)
    assert "budget_reservations" not in statements[locks[0]], "\n".join(statements)
