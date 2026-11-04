"""Acceptance cases for `budget_reserve_fast` (migration 1042), the one-round-trip admission.

Each parity case runs `reserve` and the Python path (`_reserve_slow`) on the same history, each
in a savepoint that is rolled back, and asserts the same answer, the same rows afterwards, and
the path `reserve` took (`spanlight_budget_reserve_path_total`).
"""

import asyncio
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.budgets.buckets import bump_hours_of_traces
from app.budgets.periods import period_start
from app.budgets.reserve import _FAST, BlockBudget, Refusal, _reserve_slow, reserve
from app.budgets.types import BudgetPeriod, BudgetScope
from app.core.ids import new_id
from app.core.observability import BUDGET_RESERVE_PATH
from app.db.rls import bind_project, bypass_rls
from app.db.roles import APP_FUNCTIONS
from tests.budgets.support import BudgetRef, BudgetWorld, create_budget, ingest_spend
from tests.budgets.test_snapshot_equality import (
    BIG,
    Tracked,
    closed_hour,
    ingest,
    priced_span,
    reserve_call,
)
from tests.budgets.test_snapshot_locks import _wait_blocked_by
from tests.gateway_support import metric_delta
from tests.helpers import create_project, new_span_id, new_trace_id

ESTIMATE = Decimal("0.0001")  # what `reserve_call` holds per admission, too
# Every row either path writes (reservation ids differ by design and are left out).
_ROWS = [
    text(sql)
    for sql in (
        "SELECT budget_id, period_start, settled_usd, reserved_usd, legacy_unattributed_usd "
        "FROM budget_counters ORDER BY 1, 2",
        "SELECT budget_id, bucket_start, committed_usd, generation "
        "FROM budget_bucket_snapshots ORDER BY 1, 2",
        "SELECT budget_id, bucket_start, amount_usd FROM budget_bucket_deltas ORDER BY 1, 2, 3",
        "SELECT budget_id, period_start, amount_usd, status, expires_at "
        "FROM budget_reservations ORDER BY 1, 2, 3, 4, 5",
    )
]


def _block(
    budget: BudgetRef, amount: Decimal = BIG, period: BudgetPeriod = BudgetPeriod.DAILY
) -> BlockBudget:
    return BlockBudget(budget.id, "b", period, amount)


async def _warm(world: BudgetWorld, *budgets: BudgetRef, period: str = "daily") -> None:
    """One admitted $0.0001 call: the counters and every hour row of the period exist."""
    tracked = [Tracked(budget, BudgetScope.PROJECT, None, period) for budget in budgets]
    await reserve_call(world, tracked, new_trace_id(), new_span_id())


async def _parity(
    world: BudgetWorld, blocks: list[BlockBudget], estimate: Decimal, *, path: str
) -> Refusal | None:
    """`reserve` and `_reserve_slow` on the same history: the same answer and rows."""
    project_id, now = world.workspace.project_id, world.clock.wall()
    taken = metric_delta(BUDGET_RESERVE_PATH, path=path)
    outcomes = []
    async with world.sessions() as db, db.begin():
        await bind_project(db, project_id)
        for run in (reserve, _reserve_slow):
            savepoint = await db.begin_nested()
            answer = await run(
                db,
                project_id,
                blocks,
                estimate,
                trace_id=new_trace_id(),
                span_id=new_span_id(),
                now=now,
            )
            outcomes.append((answer, [(await db.execute(rows)).all() for rows in _ROWS]))
            await savepoint.rollback()
    assert taken.value == 1
    assert outcomes[0] == outcomes[1]
    return outcomes[0][0]


async def test_a_refusal_names_the_first_budget_in_guard_order(budget_world: BudgetWorld) -> None:
    """Counters lock in id order, budgets are read in the guard's (amount, then id)."""
    world = budget_world
    larger = await create_budget(world, amount=BIG, name="Larger")
    smaller = await create_budget(world, amount=BIG, name="Smaller")
    assert larger.id < smaller.id
    await _warm(world, larger, smaller)
    await ingest_spend(world)  # a pending $0.002 delta, folded inline
    blocks = [_block(smaller, Decimal("0.0001")), _block(larger, Decimal("0.0002"))]

    refusal = await _parity(world, blocks, Decimal("0.001"), path="fast")

    assert refusal == Refusal(blocks[0], Decimal("0.0021"), Decimal("0.001"))


@pytest.mark.parametrize(
    "over",
    [Decimal(0), Decimal("0.01"), Decimal("0.00000001")],
    ids=["equal-fits", "cent-over", "least-over"],
)
async def test_committed_plus_estimate_may_equal_the_amount(
    budget_world: BudgetWorld, over: Decimal
) -> None:
    world = budget_world
    budget = await create_budget(world, amount=BIG)
    await _warm(world, budget)
    await ingest_spend(world)
    committed, estimate = Decimal("0.0021"), Decimal("0.00006789")
    block = _block(budget, committed + estimate - over)

    answer = await _parity(world, [block], estimate, path="fast")

    assert answer == (None if over == 0 else Refusal(block, committed, estimate))


async def test_a_legacy_carry_counts_and_marks_the_refusal_uncertain(
    budget_world: BudgetWorld,
) -> None:
    world = budget_world
    budget = await create_budget(world, amount=BIG)
    await _warm(world, budget)
    async with world.sessions() as db, db.begin():
        await bind_project(db, world.workspace.project_id)
        await db.execute(
            text(
                "UPDATE budget_counters SET legacy_unattributed_usd = 0.003 WHERE budget_id = :id"
            ),
            {"id": budget.id},
        )
    block = _block(budget, Decimal("0.003"))

    refusal = await _parity(world, [block], ESTIMATE, path="fast")

    assert refusal == Refusal(block, Decimal("0.0031"), ESTIMATE, legacy_uncertain=True)


@pytest.mark.parametrize(
    ("period", "column", "value", "unknown"),
    [
        pytest.param(
            "daily", "budget_history_missing_through", lambda start: start, True, id="missing"
        ),
        pytest.param(
            "daily",
            "budget_history_missing_through",
            lambda start: start - timedelta(microseconds=1),
            False,
            id="missing-before-the-period",
        ),
        pytest.param("monthly", "retention_days", lambda _: 30, True, id="monthly-30-days"),
        pytest.param("monthly", "retention_days", lambda _: 31, False, id="monthly-31-days"),
    ],
)
async def test_unknown_history_refuses(
    budget_world: BudgetWorld,
    period: str,
    column: str,
    value: Callable[[datetime], Any],
    unknown: bool,
) -> None:
    world = budget_world
    budget = await create_budget(world, period=period, amount=BIG)
    await _warm(world, budget, period=period)
    block = _block(budget, period=BudgetPeriod(period))
    async with world.sessions() as db, db.begin():
        await bypass_rls(db)
        await db.execute(
            text(f"UPDATE projects SET {column} = :value WHERE id = :id"),
            {
                "value": value(period_start(block.period, world.clock.wall())),
                "id": world.workspace.project_id,
            },
        )

    answer = await _parity(world, [block], ESTIMATE, path="fast")

    assert answer == (Refusal(block, ESTIMATE, ESTIMATE, history_unknown=True) if unknown else None)


async def test_a_stale_budget_is_never_read_after_a_refusal_and_falls_back_exactly(
    budget_world: BudgetWorld,
) -> None:
    """`a`'s hour is fresh with a pending delta, `b`'s is stale. Refusing `a` never reads `b`
    (fast). Admitting folds `a`, then hands `b` to the Python path, which must end in the rows
    the Python path alone writes."""
    world = budget_world
    a, b = [await create_budget(world, amount=BIG, name=name) for name in "ab"]
    project_id, trace_id = world.workspace.project_id, new_trace_id()
    await ingest(world, priced_span(trace_id, new_span_id(), closed_hour(world, 1)))
    await _warm(world, a, b)
    async with world.sessions() as db, db.begin():  # the trace's hour goes stale for a and b
        await bind_project(db, project_id)
        await bump_hours_of_traces(db, project_id, [trace_id])
    await _warm(world, a)  # recomputes a's hour only
    await ingest_spend(world)
    tight = _block(a, Decimal("0.001"))

    refusal = await _parity(world, [tight, _block(b)], ESTIMATE, path="fast")
    admitted = await _parity(world, [_block(a), _block(b)], ESTIMATE, path="slow")

    assert refusal == Refusal(tight, Decimal("0.0042"), ESTIMATE)  # 2 x $0.002 + 2 holds
    assert admitted is None


async def test_every_counter_is_locked_before_any_history_read(budget_world: BudgetWorld) -> None:
    """`b` is second in lock order and first in guard order. A settle holding its counter
    charges more than the estimate it held; the admission must wait for that lock before its
    first read and refuse on the charge. One statement for lock and read, the read first, or a
    lock of the first row only would read the estimate and admit."""
    world = budget_world
    a, b = [await create_budget(world, amount=BIG, name=name) for name in "ab"]
    assert a.id < b.id
    project_id, now = world.workspace.project_id, world.clock.wall()
    key = {"b": b.id, "s": period_start(BudgetPeriod.DAILY, now)}
    charge, estimate = Decimal("0.0009"), Decimal("0.0003")
    await _warm(world, a, b)
    tight = _block(b, charge + estimate - Decimal("0.0001"))
    fast = metric_delta(BUDGET_RESERVE_PATH, path="fast")
    admission_pid: asyncio.Future[int] = asyncio.get_running_loop().create_future()

    async def admit() -> Refusal | None:
        async with world.sessions() as db, db.begin():
            await bind_project(db, project_id)
            admission_pid.set_result(await db.scalar(text("SELECT pg_backend_pid()")))
            return await reserve(
                db,
                project_id,
                [tight, _block(a)],
                estimate,
                trace_id=new_trace_id(),
                span_id=new_span_id(),
                now=now,
            )

    async with world.sessions() as settler:
        await bind_project(settler, project_id)
        settler_pid = await settler.scalar(text("SELECT pg_backend_pid()"))
        await settler.execute(
            text(
                "SELECT 1 FROM budget_counters "
                "WHERE budget_id = :b AND period_start = :s FOR UPDATE"
            ),
            key,
        )
        admitting = asyncio.create_task(admit())
        await _wait_blocked_by(world, await asyncio.wait_for(admission_pid, 10), settler_pid)
        await settler.execute(
            text(
                "UPDATE budget_counters SET reserved_usd = reserved_usd - :x "
                "WHERE budget_id = :b AND period_start = :s"
            ),
            {**key, "x": ESTIMATE},
        )
        await settler.execute(
            text(
                "INSERT INTO budget_bucket_deltas "
                "(project_id, budget_id, bucket_start, amount_usd) VALUES (:p, :b, :h, :x)"
            ),
            {"p": project_id, "b": b.id, "h": closed_hour(world, 0), "x": charge},
        )
        await settler.commit()
        async with asyncio.timeout(10):
            refusal = await admitting

    assert refusal == Refusal(tight, charge, estimate)
    assert fast.value == 1


async def test_another_projects_budgets_reserve_nothing(budget_world: BudgetWorld) -> None:
    world = budget_world
    owner, ours = world.workspace.owner, world.workspace.project_id
    theirs = uuid.UUID(await create_project(owner, str(world.workspace.org_id), "Other"))
    created = await owner.post(
        f"/api/v1/projects/{theirs}/budgets",
        json={
            "name": "Theirs",
            "scope": "project",
            "period": "daily",
            "amount_usd": str(BIG),
            "action": "block",
            "channel_ids": [],
        },
    )
    assert created.status_code == 201, created.text
    block = BlockBudget(uuid.UUID(created.json()["id"]), "Theirs", BudgetPeriod.DAILY, BIG)
    now = world.clock.wall()
    started = period_start(BudgetPeriod.DAILY, now)
    async with world.sessions() as db, db.begin():  # their counter and hour rows exist
        await bind_project(db, theirs)
        assert (
            await reserve(
                db,
                theirs,
                [block],
                ESTIMATE,
                trace_id=new_trace_id(),
                span_id=new_span_id(),
                now=now,
            )
            is None
        )
    call = {
        "budget_ids": [block.id],
        "periods": [started],
        "lasts": [started],
        "amounts": [BIG],
        "monthly": [False],
        "estimate": ESTIMATE,
        "trace_id": new_trace_id(),
        "span_id": new_span_id(),
        "ids": [new_id()],
        "expires_at": now,
        "now": now,
    }

    async def held() -> tuple[Any, ...]:
        async with world.sessions() as db:
            await bypass_rls(db)
            row = await db.execute(
                text(
                    "SELECT (SELECT reserved_usd FROM budget_counters WHERE budget_id = :id), "
                    "(SELECT count(*) FROM budget_reservations WHERE budget_id = :id)"
                ),
                {"id": block.id},
            )
            return tuple(row.one())

    before = await held()
    async with world.sessions() as db, db.begin():
        await bind_project(db, ours)
        assert (await db.execute(_FAST, {**call, "project_id": ours})).one().status == "slow"
    async with world.sessions() as db:
        await bind_project(db, ours)
        with pytest.raises(DBAPIError, match="matching project binding"):
            await db.execute(_FAST, {**call, "project_id": theirs})
    assert await held() == before == (ESTIMATE, 1)


async def test_the_function_runs_as_its_caller_on_a_pinned_search_path(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    [signature] = [name for name in APP_FUNCTIONS if "budget_reserve_fast" in name]
    async with session_factory() as db:
        row = await db.execute(
            text(
                "SELECT provolatile, prosecdef, proconfig, "
                "has_function_privilege('public', oid, 'EXECUTE') "
                "FROM pg_proc WHERE oid = to_regprocedure(CAST(:signature AS text))"
            ),
            {"signature": signature},
        )
        assert tuple(row.one()) == (
            "v",
            False,
            ["search_path=pg_catalog, public, pg_temp"],
            False,
        )
