"""Budget admission from hourly snapshots equals the full period scan, on every history.

`read_history` sums every UTC hour of the period from `budget_bucket_snapshots` (valid while
the hour's `budget_bucket_versions` generation matches), folding the writers' pending
`budget_bucket_deltas` first; `scan_history` is the old full-range statement and the oracle.
Both must agree exactly (Decimal) after every step of a random history, a second read must
rewrite no snapshot row, and that second read runs one statement.

A random history (one world per seed) starts the app clock six days back and never advances it
past the real clock, and every span it ingests starts before the app clock, which ingestion
judges span times against; a step that needs more clock than is left is skipped. The
deleted-span ledger keeps only spans of the database's current UTC month, so the replay case
moves the app clock into one UTC day of that month (`replay_hours`) first.
"""

import random
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from prometheus_client import REGISTRY
from sqlalchemy import event, text

from app.budgets.history import LEASE, read_history, scan_history
from app.budgets.jobs import compact_pending_deltas, reap_expired, run_reap_budget_reservations
from app.budgets.periods import next_reset, period_start
from app.budgets.reserve import RESERVATION_LEASE, BlockBudget, reserve, settle
from app.budgets.types import BudgetPeriod, BudgetScope
from app.db.rls import bind_project, bypass_rls
from app.end_users.erasure_job import erase_end_user
from app.ingest.aggregate_changes import delete_corrective_traces
from app.ingest.pipeline import IngestTarget, ingest_spans
from tests.budgets.support import (
    PRICED_MODEL,
    BudgetRef,
    BudgetWorld,
    chat,
    create_budget,
    ingest_spend,
)
from tests.budgets.test_deleted_spend import delete_traces
from tests.gateway_support import drain_recorder, read_gateway_spans
from tests.helpers import create_api_key, create_project, new_span_id, new_trace_id, span
from tests.jobs.demo_support import run_context

HOUR = timedelta(hours=1)
OTHER_MODEL = "gpt-4.1"  # priced too: $2 per million input tokens
# What a MODEL budget on either name must and must not count: the exact names, their dated and
# `-latest` snapshots (counted), and `gpt-4.1-nano` / `gpt-4.1-mini`, which start with `gpt-4.1`
# but are other models (priced, not counted). The trigger's plpgsql scope must agree.
MODELS = (
    PRICED_MODEL,
    OTHER_MODEL,
    PRICED_MODEL + "-2025-04-14",
    OTHER_MODEL + "-20250414",
    OTHER_MODEL + "-latest",
    "gpt-4.1-nano",
)
USERS = ("u1", "u2")
BIG = Decimal("1000000")
COMPACTED = "spanlight_budget_deltas_compacted_total"


class Tracked:
    """A budget with the scope and period the test believes it has after its edits."""

    def __init__(self, ref: BudgetRef, scope: BudgetScope, scope_id: str | None, period: str):
        self.ref, self.scope, self.scope_id, self.period = ref, scope, scope_id, period

    @property
    def budget_period(self) -> BudgetPeriod:
        return BudgetPeriod(self.period)


async def snapshot_rows(world: BudgetWorld, budget_id: uuid.UUID) -> dict[datetime, datetime]:
    """bucket_start -> computed_at of the budget's snapshot rows."""
    async with world.sessions() as db:
        await bypass_rls(db)
        rows = await db.execute(
            text(
                "SELECT bucket_start, computed_at FROM budget_bucket_snapshots "
                "WHERE budget_id = :id"
            ),
            {"id": budget_id},
        )
        return {row.bucket_start: row.computed_at for row in rows}


async def snapshot_of(world: BudgetWorld, budget_id: uuid.UUID, hour: datetime) -> Decimal | None:
    async with world.sessions() as db:
        await bypass_rls(db)
        value = await db.scalar(
            text(
                "SELECT committed_usd FROM budget_bucket_snapshots "
                "WHERE budget_id = :id AND bucket_start = :hour"
            ),
            {"id": budget_id, "hour": hour},
        )
    return None if value is None else Decimal(value)


async def delta_rows(world: BudgetWorld, budget_id: uuid.UUID) -> dict[datetime, Decimal]:
    """bucket_start -> sum of the budget's pending delta rows."""
    async with world.sessions() as db:
        await bypass_rls(db)
        rows = await db.execute(
            text(
                "SELECT bucket_start, sum(amount_usd) AS amount FROM budget_bucket_deltas "
                "WHERE budget_id = :id GROUP BY bucket_start"
            ),
            {"id": budget_id},
        )
        return {row.bucket_start: Decimal(row.amount) for row in rows}


async def versions_of(world: BudgetWorld) -> dict[datetime, int]:
    async with world.sessions() as db:
        await bypass_rls(db)
        rows = await db.execute(
            text(
                "SELECT bucket_start, generation FROM budget_bucket_versions WHERE project_id = :id"
            ),
            {"id": world.workspace.project_id},
        )
        return {row.bucket_start: row.generation for row in rows}


async def in_deleted_ledger(world: BudgetWorld, span_id: str) -> bool:
    async with world.sessions() as db:
        await bypass_rls(db)
        return bool(
            await db.scalar(
                text("SELECT count(*) FROM budget_deleted_spans WHERE span_id = :id"),
                {"id": span_id},
            )
        )


async def fast_total(
    world: BudgetWorld, budget: Tracked, statements: list[str] | None = None
) -> Decimal:
    """`read_history` at the app clock; `statements` collects the statements it ran."""
    project_id, now = world.workspace.project_id, world.clock.wall()
    started = period_start(budget.budget_period, now)

    def record(_conn: Any, _cursor: Any, statement: str, *_rest: Any) -> None:
        if statements is not None:
            statements.append(statement)

    async with world.sessions() as db, db.begin():
        await bind_project(db, project_id)
        await db.execute(
            text(
                "INSERT INTO budget_counters (budget_id, period_start, project_id) "
                "VALUES (:budget_id, :started, :project_id) ON CONFLICT DO NOTHING"
            ),
            {"budget_id": budget.ref.id, "started": started, "project_id": project_id},
        )
        connection = (await db.connection()).sync_connection
        assert connection is not None
        event.listen(connection, "before_cursor_execute", record)
        try:
            total = await read_history(
                db,
                project_id,
                budget.ref.id,
                budget.scope,
                budget.scope_id,
                started,
                next_reset(budget.budget_period, now),
                now=now,
            )
        finally:
            event.remove(connection, "before_cursor_execute", record)
    return total.committed_usd


async def full_total(world: BudgetWorld, budget: Tracked) -> Decimal:
    project_id, now = world.workspace.project_id, world.clock.wall()
    async with world.sessions() as db, db.begin():
        await bind_project(db, project_id)
        total = await scan_history(
            db,
            project_id,
            budget.ref.id,
            budget.scope,
            budget.scope_id,
            period_start(budget.budget_period, now),
            next_reset(budget.budget_period, now),
        )
    return total.committed_usd


async def assert_equal(world: BudgetWorld, budgets: list[Tracked], label: str) -> None:
    """Snapshot total == full scan for every budget; a second read rewrites nothing, leaves no
    delta and runs one statement."""
    for budget in budgets:
        fast = await fast_total(world, budget)
        full = await full_total(world, budget)
        assert fast == full, f"{label}: {budget.scope.value} {fast} != {full}"
        before = await snapshot_rows(world, budget.ref.id)
        statements: list[str] = []
        assert await fast_total(world, budget, statements) == full, label
        assert len(statements) == 1, f"{label}: {budget.scope.value}\n" + "\n".join(statements)
        assert await snapshot_rows(world, budget.ref.id) == before, label
        now = world.clock.wall()
        started = period_start(budget.budget_period, now)
        ended = next_reset(budget.budget_period, now)
        pending = await delta_rows(world, budget.ref.id)
        assert {h: v for h, v in pending.items() if started <= h < ended} == {}, label


def headroom(world: BudgetWorld) -> timedelta:
    """How far the app clock may still move before it passes the real clock."""
    return datetime.now(UTC) - timedelta(minutes=2) - world.clock.wall()


def advance(world: BudgetWorld, seconds: float) -> None:
    """Move the app clock `seconds` on, but never past the real clock."""
    world.clock.advance(min(timedelta(seconds=seconds), headroom(world)).total_seconds())


def closed_hour(world: BudgetWorld, hours_ago: int) -> datetime:
    """The start of the UTC hour `hours_ago` hours before the app clock."""
    now = world.clock.wall()
    return now.replace(minute=0, second=0, microsecond=0) - HOUR * hours_ago


async def ingest(world: BudgetWorld, body: dict[str, Any]) -> None:
    response = await world.ingest.post("/v1/traces", json={"spans": [body]})
    assert response.status_code == 200, response.text
    assert response.json()["accepted"] == 1, response.text


def priced_span(
    trace_id: str,
    span_id: str,
    start: datetime,
    *,
    tokens: int = 5000,
    model: str = PRICED_MODEL,
    user: str | None = None,
    name: str = "answer",
    team: str = "a",
) -> dict[str, Any]:
    return span(
        trace_id=trace_id,
        span_id=span_id,
        start=start,
        model=model,
        usage={"input_tokens": tokens, "output_tokens": 0, "cached_tokens": 0},
        trace={
            "name": name,
            "environment": "prod",
            "user_id": user,
            "metadata": {"team": team},
        },
    )


async def reserve_call(
    world: BudgetWorld, budgets: list[Tracked], trace_id: str, span_id: str
) -> None:
    """Admit a call against every budget, as the guard does, at the app clock."""
    project_id = world.workspace.project_id
    blocks = [
        BlockBudget(b.ref.id, b.scope.value, b.budget_period, BIG, b.scope, b.scope_id)
        for b in budgets
    ]
    async with world.sessions() as db, db.begin():
        await bind_project(db, project_id)
        refusal = await reserve(
            db,
            project_id,
            blocks,
            Decimal("0.0001"),
            trace_id=trace_id,
            span_id=span_id,
            now=world.clock.wall(),
        )
    assert refusal is None, refusal


async def settle_call(world: BudgetWorld, trace_id: str, span_id: str) -> None:
    project_id = world.workspace.project_id
    async with world.sessions() as db, db.begin():
        await bind_project(db, project_id)
        await settle(db, project_id, {"trace_id": trace_id, "span_id": span_id}, world.clock.wall())


async def delete_trace(world: BudgetWorld, trace_id: str) -> None:
    project_id = world.workspace.project_id
    async with world.sessions() as db, db.begin():
        assert await delete_corrective_traces(db, project_id, [trace_id]) == 1


async def patch_budget(world: BudgetWorld, budget: Tracked, body: dict[str, Any]) -> bool:
    """Edit the budget; False when pending reservations refuse the edit (409)."""
    response = await world.workspace.owner.patch(
        f"{world.project_url}/budgets/{budget.ref.id}", json=body
    )
    assert response.status_code in (200, 409), response.text
    return response.status_code == 200


@pytest.mark.parametrize("seed", range(25))
async def test_snapshot_total_equals_full_scan_over_random_histories(
    budget_world: BudgetWorld, seed: int
) -> None:
    world = budget_world
    advance(world, (datetime.now(UTC) - timedelta(days=6) - world.clock.wall()).total_seconds())
    response = await world.workspace.owner.patch(world.project_url, json={"retention_days": 90})
    assert response.status_code == 200, response.text
    key_id = str(world.workspace.gateway_key_id)
    budgets = [
        Tracked(
            await create_budget(world, period=p, amount=BIG, scope=s.value, scope_id=i, name=n),
            s,
            i,
            p,
        )
        for s, i, n, p in (
            (BudgetScope.PROJECT, None, "project", "monthly"),
            (BudgetScope.GATEWAY_KEY, key_id, "key", "daily"),
            (BudgetScope.MODEL, PRICED_MODEL, "model", "monthly"),
            (BudgetScope.USER, "u1", "user", "daily"),
            (BudgetScope.TRACE_NAME, "answer", "trace name", "daily"),
            (BudgetScope.METADATA, "team=a", "metadata", "monthly"),
        )
    ]
    remaining = 40  # steps still to run
    stored: list[dict[str, Any]] = []  # natively ingested spans, by their last body
    deleted: list[dict[str, Any]] = []
    reaped: list[tuple[str, str]] = []

    def random_start(rng: random.Random) -> datetime:
        now = world.clock.wall()
        month = period_start(BudgetPeriod.MONTHLY, now)
        if rng.random() < 0.125:
            return month - HOUR + timedelta(minutes=30)  # the previous month's last hour
        hours = max(1, int((now - month) / HOUR))
        start = month + HOUR * rng.randrange(hours) + timedelta(minutes=rng.randrange(60))
        return min(start, now - timedelta(seconds=1))  # the month's first hour runs to now only

    async def step_ingest(rng: random.Random) -> None:
        body = priced_span(
            new_trace_id(),
            new_span_id(),
            random_start(rng),
            tokens=rng.choice((5000, 10000)),
            model=rng.choice(MODELS),
            user=rng.choice(USERS),
            team=rng.choice(("a", "b")),
        )
        await ingest(world, body)
        stored.append(body)

    async def step_reingest(rng: random.Random) -> None:
        if not stored:
            return await step_ingest(rng)
        body = dict(rng.choice(stored))
        change = rng.choice(("cost", "start", "model", "user", "name", "team"))
        if change == "cost":
            body["usage"] = {**body["usage"], "input_tokens": body["usage"]["input_tokens"] * 2}
        elif change == "start":
            start = random_start(rng)
            body["start_time"] = start.isoformat()
            body["end_time"] = (start + timedelta(milliseconds=100)).isoformat()
        elif change == "model":
            body["model"] = rng.choice([m for m in MODELS if m != body["model"]])
        elif change == "user":
            body["trace"] = {**body["trace"], "user_id": rng.choice(USERS)}
        elif change == "team":
            body["trace"] = {**body["trace"], "metadata": {"team": rng.choice(("a", "b"))}}
        else:
            body["trace"] = {**body["trace"], "name": rng.choice(("answer", "other"))}
        await ingest(world, body)
        stored[:] = [body if s["span_id"] == body["span_id"] else s for s in stored]

    async def step_replay_identical(rng: random.Random) -> None:
        if not stored:
            return await step_ingest(rng)
        before = await versions_of(world)
        body = dict(rng.choice(stored))
        await ingest(world, body)
        # A span the deleted-span ledger holds opens `bump_hours_of_traces`'s gate before the
        # write can tell the replay is identical: needless rescans of the trace's hours, totals
        # still equal.
        if not await in_deleted_ledger(world, body["span_id"]):
            assert await versions_of(world) == before

    async def step_flip_rollup(rng: random.Random) -> None:
        if not stored:
            return await step_ingest(rng)
        body = rng.choice(stored)
        async with world.sessions() as db, db.begin():  # as the usage-rollup backfill writes
            await bind_project(db, world.workspace.project_id)
            await db.execute(
                text("UPDATE spans SET usage_rollup = NOT usage_rollup WHERE span_id = :id"),
                {"id": body["span_id"]},
            )

    async def step_settled_call(rng: random.Random) -> None:
        assert (await chat(world, scripted_tokens=5000)).status_code == 200
        await drain_recorder()

    async def step_reaped_call(rng: random.Random, with_span: bool = False) -> None:
        if headroom(world) < RESERVATION_LEASE + timedelta(minutes=1):
            return await step_ingest(rng)
        trace_id, span_id = new_trace_id(), new_span_id()
        await reserve_call(world, budgets, trace_id, span_id)
        if with_span:  # the span arrived natively, nothing settled it
            await ingest(world, priced_span(trace_id, span_id, world.clock.wall()))
        advance(world, RESERVATION_LEASE.total_seconds() + 1)
        assert (await reap_expired(world.sessions, now=world.clock.wall()))[1] == 0
        reaped.append((trace_id, span_id))

    async def step_reaped_with_span(rng: random.Random) -> None:
        await step_reaped_call(rng, with_span=True)

    async def step_settle_reclassified(rng: random.Random) -> None:
        if not reaped:
            return await step_reaped_call(rng)
        await step_scope_edit(rng)
        await step_late_settle(rng)

    async def step_compact(rng: random.Random) -> None:
        await compact_pending_deltas(world.sessions)

    async def step_late_settle(rng: random.Random) -> None:
        if not reaped:
            return await step_reaped_call(rng)
        trace_id, span_id = reaped.pop(rng.randrange(len(reaped)))
        body = priced_span(
            trace_id, span_id, world.clock.wall() - timedelta(seconds=1), user=rng.choice(USERS)
        )
        await ingest(world, body)
        await settle_call(world, trace_id, span_id)

    async def step_delete_trace(rng: random.Random) -> None:
        if not stored:
            return await step_ingest(rng)
        body = stored.pop(rng.randrange(len(stored)))
        await delete_trace(world, body["trace_id"])
        deleted.append(body)

    async def step_erase_user(rng: random.Random) -> None:
        user = rng.choice(USERS)
        await erase_end_user(
            world.sessions, project_id=world.workspace.project_id, external_user_id=user, counts={}
        )
        gone = [s for s in stored if s["trace"]["user_id"] == user]
        stored[:] = [s for s in stored if s["trace"]["user_id"] != user]
        deleted.extend(gone)

    async def step_replay_deleted(rng: random.Random) -> None:
        if not deleted:
            return await step_delete_trace(rng)
        body = dict(deleted.pop(rng.randrange(len(deleted))))
        start = random_start(rng)
        body["start_time"] = start.isoformat()
        body["end_time"] = (start + timedelta(milliseconds=100)).isoformat()
        await ingest(world, body)
        stored.append(body)

    async def step_scope_edit(rng: random.Random) -> None:
        budget = rng.choice(budgets)
        if budget.scope is BudgetScope.USER:
            scope, scope_id = BudgetScope.USER, "u2" if budget.scope_id == "u1" else "u1"
        elif budget.scope is BudgetScope.MODEL:
            scope = BudgetScope.MODEL
            scope_id = OTHER_MODEL if budget.scope_id == PRICED_MODEL else PRICED_MODEL
        elif budget.scope is BudgetScope.PROJECT:
            scope, scope_id = BudgetScope.MODEL, OTHER_MODEL
        elif budget.scope is BudgetScope.TRACE_NAME:
            scope = BudgetScope.TRACE_NAME
            scope_id = "other" if budget.scope_id == "answer" else "answer"
        elif budget.scope is BudgetScope.METADATA:
            scope = BudgetScope.METADATA
            scope_id = "team=b" if budget.scope_id == "team=a" else "team=a"
        else:
            return
        if await patch_budget(world, budget, {"scope": scope.value, "scope_id": scope_id}):
            budget.scope, budget.scope_id = scope, scope_id

    async def step_period_edit(rng: random.Random) -> None:
        budget = rng.choice(budgets)
        period = "daily" if budget.period == "monthly" else "monthly"
        if await patch_budget(world, budget, {"period": period}):
            budget.period = period

    async def step_month_boundary(rng: random.Random) -> None:
        nonlocal remaining
        now = world.clock.wall()
        reset = next_reset(BudgetPeriod.MONTHLY, now) + timedelta(minutes=1)
        if headroom(world) - (reset - now) < timedelta(minutes=15) * remaining:
            return
        advance(world, (reset - now).total_seconds())

    steps = [
        step_ingest,
        step_ingest,
        step_reingest,
        step_reingest,
        step_replay_identical,
        step_flip_rollup,
        step_settled_call,
        step_reaped_call,
        step_reaped_with_span,
        step_late_settle,
        step_settle_reclassified,
        step_delete_trace,
        step_erase_user,
        step_replay_deleted,
        step_reingest,
        step_scope_edit,
        step_period_edit,
        step_month_boundary,
        step_compact,
    ]
    rng = random.Random(seed)  # noqa: S311  # reproducible histories, not secrets
    for index in range(40):
        step = rng.choice(steps)
        await step(rng)
        remaining -= 1
        # Keep enough clock for the steps still to come (a reap needs the 12-minute lease).
        spare = headroom(world) / max(remaining, 1) - timedelta(minutes=13)
        advance(world, min(rng.choice((0, 0, 1800, 3600, 7200)), max(spare.total_seconds(), 0)))
        await assert_equal(world, budgets, f"seed {seed} step {index} {step.__name__}")


async def test_ingest_committing_after_snapshot_invalidates_it(budget_world: BudgetWorld) -> None:
    world = budget_world
    budget = Tracked(await create_budget(world, amount=BIG), BudgetScope.PROJECT, None, "daily")
    hour = closed_hour(world, 3)
    body = priced_span(new_trace_id(), new_span_id(), hour + timedelta(minutes=5))
    await ingest(world, body)
    assert await fast_total(world, budget) == Decimal("0.002")
    async with world.sessions() as db, db.begin():  # an ingest correction still in flight
        await bind_project(db, world.workspace.project_id)
        await db.execute(
            text("UPDATE spans SET cost_usd = cost_usd * 2 WHERE span_id = :id"),
            {"id": body["span_id"]},
        )
        assert await fast_total(world, budget) == Decimal("0.002")  # computed without it
        assert await snapshot_of(world, budget.ref.id, hour) == Decimal("0.002")
    assert await fast_total(world, budget) == await full_total(world, budget) == Decimal("0.004")
    assert await snapshot_of(world, budget.ref.id, hour) == Decimal("0.004")


async def test_correlated_span_in_a_different_hour_than_its_admission(
    budget_world: BudgetWorld,
) -> None:
    world = budget_world
    budget = Tracked(await create_budget(world, amount=BIG), BudgetScope.PROJECT, None, "daily")
    world.clock.advance(59 * 60)  # 12:59
    admitted = closed_hour(world, 0)
    trace_id, span_id = new_trace_id(), new_span_id()
    await reserve_call(world, [budget], trace_id, span_id)
    world.clock.advance(2 * 60)  # 13:01: the span starts in the next hour
    await ingest(world, priced_span(trace_id, span_id, world.clock.wall()))
    await settle_call(world, trace_id, span_id)
    world.clock.advance(2 * 3600)  # both hours closed
    await assert_equal(world, [budget], "settled")
    assert await snapshot_of(world, budget.ref.id, admitted) == Decimal("0.002")
    assert await snapshot_of(world, budget.ref.id, admitted + HOUR) == Decimal(0)
    corrected = priced_span(trace_id, span_id, admitted + HOUR + timedelta(minutes=1), tokens=10000)
    await ingest(world, corrected)
    await assert_equal(world, [budget], "corrected")
    assert await snapshot_of(world, budget.ref.id, admitted) == Decimal("0.004")
    assert await snapshot_of(world, budget.ref.id, admitted + HOUR) == Decimal(0)


def replay_hours(now: datetime) -> tuple[datetime, datetime] | None:
    """The app clock and the first of two closed UTC hours of its day, inside now's UTC month.

    A daily budget snapshots only its own day's hours, and the deleted-span ledger keeps only the
    database's current month: today 00:00 once 02:00 has passed, otherwise yesterday 09:00 under
    a clock at yesterday noon. None before 02:00 on the first of a month, which has neither.
    """
    today = period_start(BudgetPeriod.DAILY, now)
    if now - today >= 2 * HOUR:
        return now, today
    noon = today - 12 * HOUR
    if noon < period_start(BudgetPeriod.MONTHLY, now):
        return None
    return noon, noon - 3 * HOUR


async def test_replay_of_deleted_span_into_another_hour(budget_world: BudgetWorld) -> None:
    world = budget_world
    pinned = replay_hours(datetime.now(UTC))
    if pinned is None:
        pytest.skip("before 02:00 UTC on the 1st, no day of the month has two closed hours")
    clock, hour = pinned
    world.clock.advance((clock - world.clock.wall()).total_seconds())
    budget = Tracked(await create_budget(world, amount=BIG), BudgetScope.PROJECT, None, "daily")
    body = priced_span(new_trace_id(), new_span_id(), hour + timedelta(minutes=5))
    await ingest(world, body)
    await delete_traces(world)
    await assert_equal(world, [budget], "deleted")
    assert await snapshot_of(world, budget.ref.id, hour) == Decimal("0.002")  # the ledger row
    await ingest(world, priced_span(body["trace_id"], body["span_id"], hour + HOUR))
    await assert_equal(world, [budget], "replayed")
    assert await snapshot_of(world, budget.ref.id, hour) == Decimal(0)
    assert await snapshot_of(world, budget.ref.id, hour + HOUR) == Decimal("0.002")


async def test_scope_edit_drops_the_budget_snapshots(budget_world: BudgetWorld) -> None:
    world = budget_world
    budget = Tracked(await create_budget(world, amount=BIG), BudgetScope.PROJECT, None, "daily")
    await ingest_spend(world, start=closed_hour(world, 2))
    await assert_equal(world, [budget], "before")
    assert await snapshot_rows(world, budget.ref.id)
    assert await patch_budget(world, budget, {"scope": "model", "scope_id": PRICED_MODEL})
    assert await snapshot_rows(world, budget.ref.id) == {}
    budget.scope, budget.scope_id = BudgetScope.MODEL, PRICED_MODEL
    await assert_equal(world, [budget], "after")
    assert await snapshot_rows(world, budget.ref.id)


async def test_trigger_skips_a_project_without_budgets(budget_world: BudgetWorld) -> None:
    world = budget_world
    owner = world.workspace.owner
    other = uuid.UUID(await create_project(owner, str(world.workspace.org_id), "No budgets"))
    key = await create_api_key(owner, str(other))
    response = await world.ingest.post(
        "/v1/traces",
        json={"spans": [priced_span(new_trace_id(), new_span_id(), world.clock.wall())]},
        headers={"Authorization": f"Bearer {key['secret']}"},
    )
    assert response.status_code == 200, response.text
    await create_budget(world, amount=BIG)
    await ingest_spend(world)
    async with world.sessions() as db:
        await bypass_rls(db)
        counts = {
            project_id: await db.scalar(
                text("SELECT count(*) FROM budget_bucket_deltas WHERE project_id = :id"),
                {"id": project_id},
            )
            for project_id in (other, world.workspace.project_id)
        }
    assert counts == {other: 0, world.workspace.project_id: 1}


async def test_trace_field_change_of_a_gateway_span_invalidates_the_hour(
    budget_world: BudgetWorld,
) -> None:
    """A native replay of gateway-owned span ids changes the trace's user but no span row:
    the traces trigger bumps the hour, so the USER budget's snapshot is recomputed."""
    world = budget_world
    budget = Tracked(
        await create_budget(world, amount=BIG, scope="user", scope_id="u1"),
        BudgetScope.USER,
        "u1",
        "daily",
    )
    assert (await chat(world, scripted_tokens=5000)).status_code == 200
    [gateway_span] = await read_gateway_spans(world.sessions, world.workspace.project_id)
    hour = closed_hour(world, 0)
    world.clock.advance(2 * 3600)
    await assert_equal(world, [budget], "before")
    assert await snapshot_of(world, budget.ref.id, hour) == Decimal(0)
    replay = priced_span(
        gateway_span["trace_id"], gateway_span["span_id"], gateway_span["started_at"], user="u1"
    )
    await ingest(world, replay)
    await assert_equal(world, [budget], "after")
    assert await fast_total(world, budget) == Decimal("0.002")
    assert await snapshot_of(world, budget.ref.id, hour) == Decimal("0.002")


async def test_month_rollover_reads_the_new_period(budget_world: BudgetWorld) -> None:
    world = budget_world
    budget = Tracked(
        await create_budget(world, amount=BIG, period="monthly"),
        BudgetScope.PROJECT,
        None,
        "monthly",
    )
    await ingest_spend(world, start=closed_hour(world, 3))
    await assert_equal(world, [budget], "old period")
    assert await fast_total(world, budget) == Decimal("0.002")
    rows = await snapshot_rows(world, budget.ref.id)
    now = world.clock.wall()
    world.clock.advance(
        (next_reset(BudgetPeriod.MONTHLY, now) + timedelta(minutes=1) - now).total_seconds()
    )
    await assert_equal(world, [budget], "new period")
    assert await fast_total(world, budget) == Decimal(0)
    after = await snapshot_rows(world, budget.ref.id)
    assert rows.items() <= after.items()  # the old period's rows stay, the new hours are rows


async def test_reaper_lag_snapshot_is_corrected_by_the_reap(budget_world: BudgetWorld) -> None:
    """A reservation still held after its hour closed counts nothing until the reaper counts
    it; the reap bumps the hour."""
    world = budget_world
    budget = Tracked(await create_budget(world, amount=BIG), BudgetScope.PROJECT, None, "daily")
    hour = closed_hour(world, 0)
    await reserve_call(world, [budget], new_trace_id(), new_span_id())
    world.clock.advance(72 * 60)  # 13:12: hour 12 is closed, the lease ran out, no reaper yet
    await assert_equal(world, [budget], "held")
    assert await snapshot_of(world, budget.ref.id, hour) == Decimal(0)
    assert (await reap_expired(world.sessions, now=world.clock.wall()))[0] == 1
    await assert_equal(world, [budget], "reaped")
    assert await snapshot_of(world, budget.ref.id, hour) == Decimal("0.0001")


async def test_second_read_with_nothing_touched_runs_one_statement(
    budget_world: BudgetWorld,
) -> None:
    world = budget_world
    budget = Tracked(await create_budget(world, amount=BIG), BudgetScope.PROJECT, None, "daily")
    await ingest_spend(world, start=closed_hour(world, 2))
    await ingest_spend(world)  # the open hour holds a delta too
    first: list[str] = []
    assert await fast_total(world, budget, first) == Decimal("0.004")
    assert len(first) > 1, "\n".join(first)
    second: list[str] = []
    assert await fast_total(world, budget, second) == Decimal("0.004")
    assert len(second) == 1, "\n".join(second)


async def test_delta_committed_after_recompute_snapshot_is_kept(budget_world: BudgetWorld) -> None:
    """A writer still in flight when the hour is recomputed is in neither the scan nor the
    deltas it deletes; its delta is folded by the next read."""
    world = budget_world
    budget = Tracked(await create_budget(world, amount=BIG), BudgetScope.PROJECT, None, "daily")
    hour = closed_hour(world, 2)
    await ingest(world, priced_span(new_trace_id(), new_span_id(), hour + timedelta(minutes=5)))
    target = IngestTarget(world.workspace.project_id, True, world.workspace.org_id)
    async with world.sessions() as db:  # the ingest transaction, its span and delta uncommitted
        outcome = await ingest_spans(
            db, target, [priced_span(new_trace_id(), new_span_id(), hour + timedelta(minutes=6))]
        )
        assert outcome.accepted == 1, outcome
        assert await fast_total(world, budget) == Decimal("0.002")  # recomputes the hour
        assert await snapshot_of(world, budget.ref.id, hour) == Decimal("0.002")
        await db.commit()
    assert await delta_rows(world, budget.ref.id) == {hour: Decimal("0.002")}
    assert await fast_total(world, budget) == await full_total(world, budget) == Decimal("0.004")
    assert await snapshot_of(world, budget.ref.id, hour) == Decimal("0.004")
    assert await delta_rows(world, budget.ref.id) == {}


async def test_unchanged_trace_upsert_does_not_bump_or_stale(budget_world: BudgetWorld) -> None:
    world = budget_world
    budget = Tracked(await create_budget(world, amount=BIG), BudgetScope.USER, "u1", "daily")
    hour = closed_hour(world, 2)
    body = priced_span(new_trace_id(), new_span_id(), hour + timedelta(minutes=5), user="u1")
    await ingest(world, body)
    await assert_equal(world, [budget], "stored")
    rows, versions = await snapshot_rows(world, budget.ref.id), await versions_of(world)
    await ingest(world, dict(body))  # the same batch again: nothing changed
    assert await versions_of(world) == versions
    assert await delta_rows(world, budget.ref.id) == {}
    assert await snapshot_rows(world, budget.ref.id) == rows
    statements: list[str] = []
    assert await fast_total(world, budget, statements) == Decimal("0.002")
    assert len(statements) == 1, "\n".join(statements)


async def test_settle_delta_lands_in_the_admission_hour(budget_world: BudgetWorld) -> None:
    world = budget_world
    budget = Tracked(await create_budget(world, amount=BIG), BudgetScope.PROJECT, None, "daily")
    world.clock.advance(59 * 60)  # 12:59
    admitted = closed_hour(world, 0)
    trace_id, span_id = new_trace_id(), new_span_id()
    await reserve_call(world, [budget], trace_id, span_id)
    world.clock.advance(2 * 60)  # 13:01: the span starts in the next hour
    await ingest(world, priced_span(trace_id, span_id, world.clock.wall()))
    assert await delta_rows(world, budget.ref.id) == {}  # a reserved span is priced by its settle
    await settle_call(world, trace_id, span_id)
    assert await delta_rows(world, budget.ref.id) == {admitted: Decimal("0.002")}
    await assert_equal(world, [budget], "settled")
    assert await snapshot_of(world, budget.ref.id, admitted) == Decimal("0.002")
    assert await snapshot_of(world, budget.ref.id, admitted + HOUR) == Decimal(0)


async def test_worker_compaction_matches_admission_compaction(budget_world: BudgetWorld) -> None:
    """Deltas of a budget no admission reads are folded by the reaper's job, under the same row
    lock and to the same values."""
    world = budget_world
    budget = Tracked(await create_budget(world, amount=BIG), BudgetScope.PROJECT, None, "daily")
    hours = [closed_hour(world, 3), closed_hour(world, 1), closed_hour(world, 0)]
    for hour in hours:
        await ingest(world, priced_span(new_trace_id(), new_span_id(), hour + timedelta(minutes=1)))
        await ingest(world, priced_span(new_trace_id(), new_span_id(), hour + timedelta(minutes=2)))
    assert await delta_rows(world, budget.ref.id) == dict.fromkeys(hours, Decimal("0.004"))
    before = REGISTRY.get_sample_value(COMPACTED) or 0.0
    context = await run_context(world.sessions, world.settings)
    await run_reap_budget_reservations(context, {})
    assert (REGISTRY.get_sample_value(COMPACTED) or 0.0) - before == 6
    assert await delta_rows(world, budget.ref.id) == {}
    statements: list[str] = []
    assert await fast_total(world, budget, statements) == Decimal("0.012")
    assert len(statements) > 1  # the folded rows were never computed: recomputed once
    assert await full_total(world, budget) == Decimal("0.012")
    for hour in hours:
        assert await snapshot_of(world, budget.ref.id, hour) == Decimal("0.004")
    await assert_equal(world, [budget], "compacted")


def test_history_lease_matches_the_reservation_lease() -> None:
    assert LEASE == RESERVATION_LEASE == timedelta(minutes=12)
