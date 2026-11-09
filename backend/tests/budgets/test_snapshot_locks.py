"""Lock order around the bucket-version rows (migration 1032) and the hour rows (1033).

A trace delete cascades one `spans` DELETE per trace, so the deleters bump every hour in one
sorted statement first (`app.budgets.buckets.bump_hours_of_traces`); creating or enabling a
budget locks the project `FOR UPDATE`, so no ingest transaction that skipped the bump (the
triggers skip projects without a budget) is still in flight. Writers append deltas without
locking, so none of them waits for a compactor holding an hour's row.
"""

import asyncio
import time
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import psycopg.errors
import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import OperationalError

from app.api.v1 import budgets as budgets_router
from app.budgets import jobs as budget_jobs
from app.budgets.buckets import bump_hours_of_traces
from app.budgets.jobs import _reap_batch, compact_pending_deltas, reap_expired
from app.budgets.periods import period_start
from app.budgets.reserve import BlockBudget, Refusal, reserve, settle_unstored
from app.budgets.types import BudgetPeriod, BudgetScope
from app.core.observability import BUDGET_BUSY
from app.db.rls import bind_project, bypass_rls
from app.end_users.erasure_job import erase_end_user
from app.ingest.aggregate_changes import lock_identities
from app.ingest.pipeline import IngestTarget, ingest_spans
from tests.budgets.support import BudgetWorld, create_budget, guard_decision
from tests.budgets.test_snapshot_equality import (
    BIG,
    HOUR,
    Tracked,
    assert_equal,
    closed_hour,
    fast_total,
    ingest,
    priced_span,
    reserve_call,
    settle_call,
)
from tests.gateway_support import logged, metric_delta
from tests.helpers import new_span_id, new_trace_id


async def versions(world: BudgetWorld) -> dict[datetime, int]:
    async with world.sessions() as db:
        await bypass_rls(db)
        rows = await db.execute(
            text(
                "SELECT bucket_start, generation FROM budget_bucket_versions WHERE project_id = :id"
            ),
            {"id": world.workspace.project_id},
        )
        return {row.bucket_start: row.generation for row in rows}


async def test_bump_hours_of_traces_takes_every_hour_of_the_traces(
    budget_world: BudgetWorld,
) -> None:
    world = budget_world
    budget = Tracked(await create_budget(world, amount=BIG), BudgetScope.PROJECT, None, "daily")
    hour = closed_hour(world, 0)
    a, b = new_trace_id(), new_trace_id()
    await ingest(world, priced_span(a, new_span_id(), hour - 3 * HOUR))
    await ingest(world, priced_span(a, new_span_id(), hour - 1 * HOUR))
    await ingest(world, priced_span(b, new_span_id(), hour - 2 * HOUR))
    await reserve_call(world, [budget], a, new_span_id())  # admitted in `hour`
    before = await versions(world)
    assert before == {}  # untracked writes and an admission are deltas and nothing, not bumps
    async with world.sessions() as db, db.begin():
        await bind_project(db, world.workspace.project_id)
        await bump_hours_of_traces(db, world.workspace.project_id, [b, a])
    after = await versions(world)
    assert set(after) == {hour - 3 * HOUR, hour - 2 * HOUR, hour - 1 * HOUR, hour}
    assert all(after[h] > before.get(h, 0) for h in after)


@pytest.mark.serial
async def test_erasure_and_an_ingest_batch_over_the_same_hours_both_complete(
    budget_world: BudgetWorld,
) -> None:
    """An ingest batch holds the sorted version rows of {H-1, H}; an erasure of traces in
    hours H and H-1 waits for it instead of deadlocking inside the cascade."""
    world = budget_world
    await create_budget(world, amount=BIG)
    project_id = world.workspace.project_id
    hour = closed_hour(world, 0)
    first, second = new_trace_id(), new_trace_id()
    await ingest(world, priced_span(first, new_span_id(), hour + timedelta(minutes=1), user="u1"))
    await ingest(world, priced_span(second, new_span_id(), hour - HOUR, user="u1"))
    async with world.sessions() as batch:  # the ingest batch, its hours taken and not committed
        await bind_project(batch, project_id)
        await bump_hours_of_traces(batch, project_id, [first, second])
        erasure = asyncio.create_task(
            erase_end_user(world.sessions, project_id=project_id, external_user_id="u1", counts={})
        )
        await asyncio.sleep(0.5)
        assert not erasure.done()
        await batch.commit()
        async with asyncio.timeout(10):
            await erasure
    async with world.sessions() as db:
        await bypass_rls(db)
        left = await db.scalar(
            text("SELECT count(*) FROM traces WHERE project_id = :id"), {"id": project_id}
        )
    assert left == 0


async def test_erasure_locks_identities_and_trace_rows_before_any_version_row(
    budget_world: BudgetWorld,
) -> None:
    """Ingest locks a trace's identity, then its row, then the version rows (`write_spans`);
    an erasure batch takes them in the same order, so the two cannot cycle."""
    world = budget_world
    await create_budget(world, amount=BIG)
    project_id = world.workspace.project_id
    await ingest(
        world, priced_span(new_trace_id(), new_span_id(), closed_hour(world, 0), user="u1")
    )
    statements: list[str] = []

    def record(_conn: Any, _cursor: Any, statement: str, *_rest: Any) -> None:
        statements.append(statement)

    engine = world.sessions.kw["bind"].sync_engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        await erase_end_user(
            world.sessions, project_id=project_id, external_user_id="u1", counts={}
        )
    finally:
        event.remove(engine, "before_cursor_execute", record)

    def first(*markers: str) -> int:
        found = (i for i, s in enumerate(statements) if all(m in s for m in markers))
        return next(found, len(statements))  # missing sorts last

    bump = first("INSERT INTO budget_bucket_versions")
    identities = first("pg_advisory_xact_lock", "AS u(k)")
    rows = first("FROM traces WHERE project_id", "FOR UPDATE")
    assert identities < rows < bump, "\n".join(statements)


@pytest.mark.serial
async def test_erasure_waits_for_an_ingest_batch_holding_the_users_trace_rows(
    budget_world: BudgetWorld,
) -> None:
    """An ingest batch holds the user's identities and trace rows and then bumps its hours; the
    erasure waits on the trace rows without holding a version row the batch needs."""
    world = budget_world
    await create_budget(world, amount=BIG)
    project_id = world.workspace.project_id
    hour = closed_hour(world, 0)
    first, second = new_trace_id(), new_trace_id()
    await ingest(world, priced_span(first, new_span_id(), hour + timedelta(minutes=1), user="u1"))
    await ingest(world, priced_span(second, new_span_id(), hour - HOUR, user="u1"))
    async with world.sessions() as batch:  # as `write_spans` does, up to its bump
        await bind_project(batch, project_id)
        await batch.execute(
            text("SELECT id FROM projects WHERE id = :id FOR KEY SHARE"), {"id": project_id}
        )
        await lock_identities(batch, project_id, [first, second])
        erasure = asyncio.create_task(
            erase_end_user(world.sessions, project_id=project_id, external_user_id="u1", counts={})
        )
        await asyncio.sleep(0.5)
        assert not erasure.done()
        async with asyncio.timeout(5):  # a cycle would make Postgres abort one side here
            await bump_hours_of_traces(batch, project_id, [first, second], [hour])
        await batch.commit()
        async with asyncio.timeout(10):
            await erasure
    async with world.sessions() as db:
        await bypass_rls(db)
        left = await db.scalar(
            text("SELECT count(*) FROM traces WHERE project_id = :id"), {"id": project_id}
        )
    assert left == 0


@pytest.mark.serial
async def test_creating_a_budget_waits_for_an_open_ingest_transaction(
    budget_world: BudgetWorld,
) -> None:
    world = budget_world
    project_id = world.workspace.project_id
    async with world.sessions() as ingesting:  # what every ingest transaction holds
        await bind_project(ingesting, project_id)
        await ingesting.execute(
            text("SELECT id FROM projects WHERE id = :id FOR KEY SHARE"), {"id": project_id}
        )
        creation = asyncio.create_task(create_budget(world, amount=Decimal("1")))
        await asyncio.sleep(0.5)
        assert not creation.done()
        await ingesting.commit()
        async with asyncio.timeout(10):
            budget = await creation
    assert isinstance(budget.id, uuid.UUID)


async def test_ingest_takes_its_version_rows_in_one_statement_before_the_trace_upsert(
    budget_world: BudgetWorld,
) -> None:
    """The traces and spans triggers each bump rows one statement at a time; the batch takes
    every hour it touches in one sorted statement first, so they find rows it already holds."""
    world = budget_world
    await create_budget(world, amount=BIG)
    project_id = world.workspace.project_id
    hour = closed_hour(world, 0)
    trace_id = new_trace_id()
    await ingest(world, priced_span(trace_id, new_span_id(), hour - HOUR))
    statements: list[str] = []

    def record(_conn: Any, _cursor: Any, statement: str, *_rest: Any) -> None:
        statements.append(statement)

    target = IngestTarget(project_id, True, world.workspace.org_id)
    moved = priced_span(trace_id, new_span_id(), hour + timedelta(minutes=1), name="other")
    async with world.sessions() as db:
        connection = (await db.connection()).sync_connection
        assert connection is not None
        event.listen(connection, "before_cursor_execute", record)
        try:
            outcome = await ingest_spans(db, target, [moved])
        finally:
            event.remove(connection, "before_cursor_execute", record)
        await db.commit()
    assert outcome.accepted == 1, outcome
    bumps = [i for i, s in enumerate(statements) if "INSERT INTO budget_bucket_versions" in s]
    traces = [i for i, s in enumerate(statements) if s.startswith("INSERT INTO traces")]
    assert len(bumps) == 1 and bumps[0] < traces[0], "\n".join(statements)
    # The renamed trace's current span hour is bumped (by the traces trigger); the new span is
    # untracked and priced as a delta of its own hour.
    assert set(await versions(world)) == {hour - HOUR}
    async with world.sessions() as db:
        await bypass_rls(db)
        delta_hours = await db.scalars(
            text("SELECT bucket_start FROM budget_bucket_deltas WHERE project_id = :id"),
            {"id": project_id},
        )
    assert set(delta_hours) == {hour - HOUR, hour}  # both spans' deltas, nothing folded them


@pytest.mark.serial
async def test_two_ingest_batches_over_crossed_hours_both_complete(
    budget_world: BudgetWorld,
) -> None:
    """Batch A (trace T: span in H1, adds H2, renames) holds its sorted rows {H1, H2}; batch B
    (trace U: span in H2, adds a late span in H1, renames) waits for them and completes."""
    world = budget_world
    await create_budget(world, amount=BIG)
    project_id = world.workspace.project_id
    h2 = closed_hour(world, 0)
    h1 = h2 - HOUR
    t, u = new_trace_id(), new_trace_id()
    await ingest(world, priced_span(t, new_span_id(), h1))
    await ingest(world, priced_span(u, new_span_id(), h2))
    async with world.sessions() as batch_a:
        await bind_project(batch_a, project_id)
        await bump_hours_of_traces(batch_a, project_id, [t], [h2])  # as write_spans does first
        batch_b = asyncio.create_task(
            ingest(world, priced_span(u, new_span_id(), h1 + timedelta(minutes=1), name="other"))
        )
        await asyncio.sleep(0.5)
        assert not batch_b.done()
        await batch_a.commit()
        async with asyncio.timeout(10):
            await batch_b
    assert set(await versions(world)) == {h1, h2}


@pytest.mark.serial
async def test_writers_do_not_wait_for_a_held_snapshot_row(budget_world: BudgetWorld) -> None:
    """A compactor (admission or the reaper's job) holds the open hour's row; an ingest batch
    and a settle of that hour append their deltas and complete without waiting."""
    world = budget_world
    budget = Tracked(await create_budget(world, amount=BIG), BudgetScope.PROJECT, None, "daily")
    project_id = world.workspace.project_id
    hour = closed_hour(world, 0)
    await ingest(world, priced_span(new_trace_id(), new_span_id(), hour + timedelta(minutes=1)))
    assert await fast_total(world, budget) == Decimal("0.002")  # the hour's row exists
    trace_id, span_id = new_trace_id(), new_span_id()
    await reserve_call(world, [budget], trace_id, span_id)
    async with world.sessions() as compactor:
        await bind_project(compactor, project_id)
        await compactor.execute(
            text(
                "SELECT 1 FROM budget_bucket_snapshots WHERE budget_id = :id "
                "AND bucket_start = :hour FOR UPDATE"
            ),
            {"id": budget.ref.id, "hour": hour},
        )
        async with asyncio.timeout(5):
            await ingest(
                world, priced_span(new_trace_id(), new_span_id(), hour + timedelta(minutes=2))
            )
            await ingest(world, priced_span(trace_id, span_id, hour + timedelta(minutes=3)))
            await settle_call(world, trace_id, span_id)
        async with world.sessions() as db:
            await bypass_rls(db)
            pending = await db.scalar(
                text("SELECT count(*) FROM budget_bucket_deltas WHERE budget_id = :id"),
                {"id": budget.ref.id},
            )
        assert pending == 2  # the second span's cost and the settled charge
        await compactor.rollback()
    await assert_equal(world, [budget], "released")
    assert await fast_total(world, budget) == Decimal("0.006")


@pytest.mark.serial
@pytest.mark.parametrize("edit", ["scope", "delete"])
async def test_budget_edit_waits_for_an_ingest_batch_holding_its_delta(
    budget_world: BudgetWorld, edit: str
) -> None:
    """An ingest batch holds `KEY SHARE` on the project before the edit holds the budget; the
    edit's fence then waits for the batch, whose delta insert takes `KEY SHARE` on the budget
    through its foreign key: compatible with the edit's `FOR NO KEY UPDATE`, a deadlock under
    `FOR UPDATE`."""
    world = budget_world
    budget = await create_budget(world, amount=BIG)
    project_id = world.workspace.project_id
    target = IngestTarget(project_id, True, world.workspace.org_id)
    async with world.sessions() as batch:
        await bind_project(batch, project_id)
        await batch.execute(  # what every batch takes first (`lock_trace_identities`)
            text("SELECT id FROM projects WHERE id = :id FOR KEY SHARE"), {"id": project_id}
        )
        url = f"{world.project_url}/budgets/{budget.id}"
        if edit == "scope":
            request = world.workspace.owner.patch(url, json={"scope": "user", "scope_id": "u1"})
        else:
            request = world.workspace.owner.delete(url)
        editing = asyncio.create_task(request)
        await asyncio.sleep(0.5)
        assert not editing.done()  # the edit holds the budget and waits on the fence
        async with asyncio.timeout(10):  # the batch's delta insert must not wait on the edit
            outcome = await ingest_spans(
                batch, target, [priced_span(new_trace_id(), new_span_id(), closed_hour(world, 0))]
            )
            assert outcome.accepted == 1, outcome
            await batch.commit()
            response = await editing
    assert response.status_code == (200 if edit == "scope" else 204), response.text


@pytest.mark.serial
async def test_worker_compaction_commits_each_budget_on_its_own(budget_world: BudgetWorld) -> None:
    """One transaction per budget: a budget whose row is gone by the time its turn comes is
    skipped, and the other budget's deltas are folded and committed regardless."""
    world = budget_world
    first = await create_budget(world, amount=BIG, name="first")
    second = await create_budget(world, amount=BIG, name="second")
    hour = closed_hour(world, 0)
    await ingest(world, priced_span(new_trace_id(), new_span_id(), hour + timedelta(minutes=1)))
    await ingest(world, priced_span(new_trace_id(), new_span_id(), hour + timedelta(minutes=2)))
    async with world.sessions() as deleting:  # the budget's row locked by an uncommitted delete
        await bypass_rls(deleting)
        await deleting.execute(text("DELETE FROM budgets WHERE id = :id"), {"id": first.id})
        folding = asyncio.create_task(compact_pending_deltas(world.sessions))
        await asyncio.sleep(0.5)
        assert not folding.done()  # waiting on the first budget's row
        await deleting.commit()
        async with asyncio.timeout(10):
            folded = await folding
    assert folded == 2  # the second budget's two deltas; the first budget was skipped
    async with world.sessions() as db:
        await bypass_rls(db)
        pending = await db.scalar(
            text("SELECT count(*) FROM budget_bucket_deltas WHERE project_id = :id"),
            {"id": world.workspace.project_id},
        )
        total = await db.scalar(
            text("SELECT committed_usd FROM budget_bucket_snapshots WHERE budget_id = :id"),
            {"id": second.id},
        )
    assert pending == 0
    assert Decimal(total) == Decimal("0.004")


async def test_worker_compaction_defers_a_budget_the_database_refuses_for_now(
    budget_world: BudgetWorld, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A lock timeout or deadlock on one budget's fold leaves that budget to the next minute;
    the other budgets are still folded and committed in the same pass."""
    world = budget_world
    first = await create_budget(world, amount=BIG, name="first")
    await create_budget(world, amount=BIG, name="second")
    hour = closed_hour(world, 0)
    await ingest(world, priced_span(new_trace_id(), new_span_id(), hour + timedelta(minutes=1)))
    await ingest(world, priced_span(new_trace_id(), new_span_id(), hour + timedelta(minutes=2)))
    fold = budget_jobs.compact_budget_deltas

    async def refused(db: Any, project_id: Any, budget_id: Any, *args: Any, **kwargs: Any) -> Any:
        if budget_id == first.id:
            raise OperationalError(
                "UPDATE budget_bucket_snapshots", {}, psycopg.errors.LockNotAvailable()
            )
        return await fold(db, project_id, budget_id, *args, **kwargs)

    monkeypatch.setattr(budget_jobs, "compact_budget_deltas", refused)

    async with asyncio.timeout(10):
        folded = await compact_pending_deltas(world.sessions)

    assert folded == 2  # the second budget's two deltas
    async with world.sessions() as db:
        await bypass_rls(db)
        pending = (
            await db.execute(
                text(
                    "SELECT budget_id, count(*) AS deltas FROM budget_bucket_deltas "
                    "WHERE project_id = :id GROUP BY budget_id"
                ),
                {"id": world.workspace.project_id},
            )
        ).all()
    assert {row.budget_id: row.deltas for row in pending} == {first.id: 2}
    [event] = logged(caplog, "budget_deltas_fold_deferred")
    assert event["budget_id"] == str(first.id)


async def test_reaper_locks_a_calls_reservations_in_settle_order(budget_world: BudgetWorld) -> None:
    """Settle locks a call's reservation rows in budget order. The guard holds budgets by
    amount, so a call's row ids need not follow its budget ids: a reaper locking the rows in
    id order deadlocks with a settle that runs at the lease's end."""
    world = budget_world
    project_id = world.workspace.project_id
    a = await create_budget(world, amount=BIG, name="a")
    b = await create_budget(world, amount=BIG, name="b")
    assert a.id < b.id
    admitted = closed_hour(world, 1) + timedelta(minutes=5)  # the lease ran out an hour ago
    trace_id, span_id = new_trace_id(), new_span_id()
    async with world.sessions() as db, db.begin():
        await bind_project(db, project_id)
        blocks = [  # b's row first: its id is the lower one
            BlockBudget(budget.id, name, BudgetPeriod.DAILY, BIG, BudgetScope.PROJECT, None)
            for name, budget in (("b", b), ("a", a))
        ]
        refusal = await reserve(
            db,
            project_id,
            blocks,
            Decimal("0.0001"),
            trace_id=trace_id,
            span_id=span_id,
            now=admitted,
        )
        assert refusal is None
    async with world.sessions() as db:
        await bypass_rls(db)
        rows = (
            await db.execute(
                text(
                    "SELECT id, budget_id FROM budget_reservations WHERE trace_id = :t ORDER BY id"
                ),
                {"t": trace_id},
            )
        ).all()
    assert [row.budget_id for row in rows] == [b.id, a.id]  # id order is not budget order
    reaper_pid: asyncio.Future[int] = asyncio.get_running_loop().create_future()

    async def reap() -> int:
        async with world.sessions() as db:
            await bypass_rls(db)
            reaper_pid.set_result(await db.scalar(text("SELECT pg_backend_pid()")))
            reaped = await _reap_batch(db, world.clock.wall())
            await db.commit()
            return reaped

    async with world.sessions() as settler:  # a settle holding its first row, in budget order
        await bind_project(settler, project_id)
        settler_pid = await settler.scalar(text("SELECT pg_backend_pid()"))
        await settler.execute(
            text("SELECT id FROM budget_reservations WHERE id = :id FOR UPDATE"), {"id": rows[1].id}
        )
        reaping = asyncio.create_task(reap())
        await _wait_blocked_by(world, await asyncio.wait_for(reaper_pid, 10), settler_pid)
        async with asyncio.timeout(10):  # its second row: free, unless the reaper holds it
            await settler.execute(
                text("SELECT id FROM budget_reservations WHERE id = :id FOR UPDATE"),
                {"id": rows[0].id},
            )
            await settler.commit()
            assert await reaping == 2


@pytest.mark.serial
@pytest.mark.parametrize("worker", ["reaper", "settle_unstored"])
async def test_reaper_and_unstored_settle_wait_for_a_budget_delete_fence(
    budget_world: BudgetWorld, worker: str, caplog: pytest.LogCaptureFixture
) -> None:
    """`delete_budget` holds the budget `FOR NO KEY UPDATE` and the project `FOR UPDATE`, then
    cascades into the reservation rows. The reaper and `settle_unstored` take the project
    `KEY SHARE` before any reservation row, so they queue on the fence instead of holding a
    row the cascade wants while their trigger's version-row (or delta) insert waits on the
    project."""
    world = budget_world
    budget = await create_budget(world, amount=BIG)
    project_id = world.workspace.project_id
    admitted = closed_hour(world, 1) + timedelta(minutes=5)  # the lease ran out an hour ago
    trace_id, span_id = new_trace_id(), new_span_id()
    async with world.sessions() as db, db.begin():
        await bind_project(db, project_id)
        block = BlockBudget(budget.id, "b", BudgetPeriod.DAILY, BIG, BudgetScope.PROJECT, None)
        assert (
            await reserve(
                db,
                project_id,
                [block],
                Decimal("0.0001"),
                trace_id=trace_id,
                span_id=span_id,
                now=admitted,
            )
            is None
        )
    async with world.sessions() as db, db.begin():  # the admission hour has no version row
        await bypass_rls(db)
        await db.execute(
            text("DELETE FROM budget_bucket_versions WHERE project_id = :p"), {"p": project_id}
        )
    async with world.sessions() as editor:  # delete_budget up to its fence
        await bind_project(editor, project_id)
        await editor.execute(
            text("SELECT id FROM budgets WHERE id = :id FOR NO KEY UPDATE"), {"id": budget.id}
        )
        await editor.execute(
            text("SELECT id FROM projects WHERE id = :id FOR UPDATE"), {"id": project_id}
        )
        if worker == "reaper":
            working = asyncio.create_task(reap_expired(world.sessions, now=world.clock.wall()))
        else:
            working = asyncio.create_task(
                settle_unstored(
                    world.sessions, project_id, {"trace_id": trace_id, "span_id": span_id}
                )
            )
        await asyncio.sleep(0.5)
        assert not working.done()  # queued on the project, holding no reservation row
        async with asyncio.timeout(10):
            await editor.execute(text("DELETE FROM budgets WHERE id = :id"), {"id": budget.id})
            await editor.commit()
            await working
    async with world.sessions() as db:
        await bypass_rls(db)
        left = await db.scalar(
            text("SELECT count(*) FROM budget_reservations WHERE project_id = :p"),
            {"p": project_id},
        )
    assert left == 0  # the cascade took the row; nothing was reaped or settled afterwards
    assert logged(caplog, "budget.settle_unstored_failed") == []  # best effort, but no deadlock


async def test_admissions_share_the_budget_row_and_wait_for_an_edit(
    budget_world: BudgetWorld,
) -> None:
    """The guard holds the budget `FOR SHARE`: a second admission's guard read does not wait
    for one in flight (concurrent admissions serialize later, on the counter row), while an
    edit's `FOR NO KEY UPDATE` still excludes the guard and keeps its order (the edit's fence
    comes after its budget lock)."""
    world = budget_world
    budget = await create_budget(world, amount=BIG)
    project_id = world.workspace.project_id
    now = world.clock.wall()
    async with world.sessions() as other:  # an admission in flight
        await bind_project(other, project_id)
        await other.execute(
            text("SELECT id FROM budgets WHERE id = :id FOR SHARE"), {"id": budget.id}
        )
        async with asyncio.timeout(5):
            assert (await guard_decision(world, now)).allowed
    async with world.sessions() as editor:  # `lock_budget`, before its fence
        await bind_project(editor, project_id)
        await editor.execute(
            text("SELECT id FROM budgets WHERE id = :id FOR NO KEY UPDATE"), {"id": budget.id}
        )
        checking = asyncio.create_task(guard_decision(world, now))
        await asyncio.sleep(0.5)
        assert not checking.done()  # queued behind the edit
        await editor.rollback()
        async with asyncio.timeout(5):
            assert (await checking).allowed


async def test_an_admission_waiting_on_the_counter_sees_a_settle_that_commits_meanwhile(
    budget_world: BudgetWorld,
) -> None:
    """The counter lock is its own statement, before the hours read: a settle that moves a
    held estimate from `reserved_usd` into a delta row while the admission waits for the lock
    is seen whole by the read (both or neither), never half (counter without, hours without)."""
    world = budget_world
    budget = await create_budget(world, amount=BIG)
    project_id, now = world.workspace.project_id, world.clock.wall()
    started = period_start(BudgetPeriod.DAILY, now)
    hour = now.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    held, estimate = Decimal("0.0005"), Decimal("0.0003")
    block = BlockBudget(budget.id, "b", BudgetPeriod.DAILY, BIG, BudgetScope.PROJECT, None)
    async with world.sessions() as db, db.begin():  # one call admitted: its estimate is held
        await bind_project(db, project_id)
        assert (
            await reserve(
                db,
                project_id,
                [block],
                held,
                trace_id=new_trace_id(),
                span_id=new_span_id(),
                now=now,
            )
            is None
        )
    tight = BlockBudget(
        budget.id, "b", BudgetPeriod.DAILY, held + estimate - Decimal("0.0001"), BudgetScope.PROJECT
    )
    async with world.sessions() as settler:
        await bind_project(settler, project_id)
        await settler.execute(  # the settle holds the counter row
            text(
                "SELECT 1 FROM budget_counters "
                "WHERE budget_id = :b AND period_start = :s FOR UPDATE"
            ),
            {"b": budget.id, "s": started},
        )

        admission_pid: asyncio.Future[int] = asyncio.get_running_loop().create_future()

        async def admit() -> Refusal | None:
            async with world.sessions() as db, db.begin():
                await bind_project(db, project_id)
                admission_pid.set_result(await db.scalar(text("SELECT pg_backend_pid()")))
                return await reserve(
                    db,
                    project_id,
                    [tight],
                    estimate,
                    trace_id=new_trace_id(),
                    span_id=new_span_id(),
                    now=now,
                )

        admitting = asyncio.create_task(admit())
        pid = await asyncio.wait_for(admission_pid, 10)
        for _ in range(200):  # the admission is in a lock wait on the counter before the settle
            # A fresh transaction per look: `pg_stat_activity` is one snapshot per transaction.
            async with world.sessions() as watch:
                waiting = await watch.scalar(
                    text(
                        "SELECT wait_event_type = 'Lock' FROM pg_stat_activity "
                        "WHERE pid = :pid AND (query LIKE '%budget_counters%' "
                        "OR query LIKE '%budget_reserve_fast%')"
                    ),
                    {"pid": pid},
                )
            if waiting:
                break
            await asyncio.sleep(0.05)
        else:
            raise AssertionError("the admission never waited for the counter row")
        assert not admitting.done()
        # The settle: the estimate leaves the counter and the charge lands in the admission hour.
        await settler.execute(
            text(
                "UPDATE budget_counters SET reserved_usd = reserved_usd - :x "
                "WHERE budget_id = :b AND period_start = :s"
            ),
            {"x": held, "b": budget.id, "s": started},
        )
        await settler.execute(
            text(
                "INSERT INTO budget_bucket_deltas "
                "(project_id, budget_id, bucket_start, amount_usd) VALUES (:p, :b, :h, :x)"
            ),
            {"p": project_id, "b": budget.id, "h": hour, "x": held},
        )
        await settler.commit()
        async with asyncio.timeout(10):
            refusal = await admitting
    # The period holds `held` either way; with the estimate it is over the tight cap.
    assert refusal is not None and refusal.committed_usd == held, refusal


async def _wait_blocked_by(world: BudgetWorld, pid: int, blocker: int) -> None:
    """Wait until backend `pid` is in a lock wait on backend `blocker`."""
    async with asyncio.timeout(10):
        while True:
            async with world.sessions() as watch:
                blocked = await watch.scalar(
                    text("SELECT :blocker = ANY(pg_blocking_pids(:pid))"),
                    {"pid": pid, "blocker": blocker},
                )
            if blocked:
                return
            await asyncio.sleep(0.02)


async def test_first_admissions_of_a_period_over_overlapping_budgets_do_not_deadlock(
    budget_world: BudgetWorld,
) -> None:
    """At a period rollover, admissions sharing some budgets (a project budget plus per-key
    budgets) create the period's counter rows together. Y ({a, b, c}) inserts `b` while it
    waits for other inserters of `a` and `c`; X ({a, b}) starts after `a` is committed and
    waits for Y's `b`. Neither may hold a counter lock the other needs: both are admitted."""
    world = budget_world
    a, b, c = [await create_budget(world, amount=BIG, name=name) for name in "abc"]
    assert a.id < b.id < c.id
    project_id, now = world.workspace.project_id, world.clock.wall()
    started = period_start(BudgetPeriod.DAILY, now)

    def block(budget: Any) -> BlockBudget:
        return BlockBudget(budget.id, "b", BudgetPeriod.DAILY, BIG)

    async def admit(budgets: list[Any], pid: asyncio.Future[int]) -> Refusal | None:
        async with world.sessions() as db, db.begin():
            await bind_project(db, project_id)
            pid.set_result(await db.scalar(text("SELECT pg_backend_pid()")))
            return await reserve(
                db,
                project_id,
                [block(budget) for budget in budgets],
                Decimal("0.0001"),
                trace_id=new_trace_id(),
                span_id=new_span_id(),
                now=now,
            )

    async def insert_counter(db: Any, budget: Any) -> int:
        await bind_project(db, project_id)
        await db.execute(
            text(
                "INSERT INTO budget_counters (budget_id, period_start, project_id) "
                "VALUES (:b, :s, :p)"
            ),
            {"b": budget.id, "s": started, "p": project_id},
        )
        pid = await db.scalar(text("SELECT pg_backend_pid()"))
        assert isinstance(pid, int)
        return pid

    loop = asyncio.get_running_loop()
    y_pid, x_pid = loop.create_future(), loop.create_future()
    tasks: list[asyncio.Task[Refusal | None]] = []
    async with world.sessions() as first, world.sessions() as last:
        try:
            first_pid = await insert_counter(first, a)  # other first admissions in flight
            last_pid = await insert_counter(last, c)
            tasks.append(asyncio.create_task(admit([a, b, c], y_pid)))
            y = await asyncio.wait_for(y_pid, 10)
            await _wait_blocked_by(world, y, first_pid)
            await first.commit()
            await _wait_blocked_by(world, y, last_pid)  # Y now holds its insert of `b`
            tasks.append(asyncio.create_task(admit([a, b], x_pid)))
            await _wait_blocked_by(world, await asyncio.wait_for(x_pid, 10), y)
            await last.commit()
            async with asyncio.timeout(10):
                results = await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    assert results == [None, None]

    # Once the rows exist, an admission over several budgets locks them in its first statement.
    statements: list[str] = []

    def record(_conn: Any, _cursor: Any, statement: str, *_rest: Any) -> None:
        if "budget_counters" in statement or "budget_reserve_fast" in statement:
            statements.append(statement)

    engine = world.sessions.kw["bind"].sync_engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        assert await admit([a, b, c], loop.create_future()) is None
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert not any("INSERT INTO budget_counters" in s for s in statements), statements
    assert len(statements) == 1 and "budget_reserve_fast" in statements[0], statements


async def test_a_refusal_names_the_lowest_amount_budget(budget_world: BudgetWorld) -> None:
    """Several budgets refuse: the refusal is the first in the guard's order (amount, then id),
    whatever order the counter rows are locked in."""
    world = budget_world
    larger = await create_budget(world, amount=Decimal("0.0002"), name="Larger")
    smaller = await create_budget(world, amount=Decimal("0.0001"), name="Smaller")
    assert larger.id < smaller.id  # ids are time-ordered: lock order is not amount order
    project_id, now = world.workspace.project_id, world.clock.wall()
    blocks = [  # the guard's order: amount, then id
        BlockBudget(smaller.id, "Smaller", BudgetPeriod.DAILY, Decimal("0.0001")),
        BlockBudget(larger.id, "Larger", BudgetPeriod.DAILY, Decimal("0.0002")),
    ]
    async with world.sessions() as db, db.begin():
        await bind_project(db, project_id)
        refusal = await reserve(
            db,
            project_id,
            blocks,
            Decimal("0.001"),
            trace_id=new_trace_id(),
            span_id=new_span_id(),
            now=now,
        )
    assert refusal is not None and refusal.budget.id == smaller.id


async def test_an_edit_blocked_by_admissions_answers_budget_busy(
    budget_world: BudgetWorld, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """An edit's `FOR NO KEY UPDATE` waits for the admissions holding the budget `FOR SHARE`,
    and new admissions do not queue behind it; so the edit tries a few times with a short lock
    timeout and then answers `503 BUDGET_BUSY` with `Retry-After` instead of hanging on the
    statement timeout, logging and counting every timed-out try. Once the admissions are gone
    the same edit goes through."""
    monkeypatch.setattr(budgets_router, "BUSY_LOCK_TIMEOUT_MS", 200)
    world = budget_world
    budget = await create_budget(world, amount=BIG)
    project_id = world.workspace.project_id
    url = f"{world.project_url}/budgets/{budget.id}"
    retried = metric_delta(BUDGET_BUSY, outcome="retried")
    refused = metric_delta(BUDGET_BUSY, outcome="refused")
    async with world.sessions() as admission:
        await bind_project(admission, project_id)
        await admission.execute(
            text("SELECT id FROM budgets WHERE id = :id FOR SHARE"), {"id": budget.id}
        )
        started = time.monotonic()
        async with asyncio.timeout(5):
            response = await world.workspace.owner.patch(url, json={"amount_usd": "0.005"})
        elapsed = time.monotonic() - started
    assert response.status_code == 503, response.text
    assert response.json()["code"] == "BUDGET_BUSY"
    assert "other requests" in response.json()["detail"]
    assert response.headers["Retry-After"]
    assert elapsed >= 0.2 * budgets_router.BUSY_ATTEMPTS  # it did try more than once
    assert (retried.value, refused.value) == (budgets_router.BUSY_ATTEMPTS - 1, 1)
    events = logged(caplog, "budget_busy")
    assert [event["outcome"] for event in events] == ["retried"] * (
        budgets_router.BUSY_ATTEMPTS - 1
    ) + ["refused"]
    assert {event["budget_id"] for event in events} == {str(budget.id)}
    response = await world.workspace.owner.patch(url, json={"amount_usd": "0.005"})
    assert response.status_code == 200, response.text


async def test_an_edit_waiting_on_the_organization_answers_budget_busy(
    budget_world: BudgetWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The organization and project locks a budget write takes first sit inside the bounded
    tries too: a writer holding the organization past the lock timeout makes the edit answer
    `503 BUDGET_BUSY`, never a 500 for the lock wait."""
    monkeypatch.setattr(budgets_router, "BUSY_LOCK_TIMEOUT_MS", 200)
    world = budget_world
    budget = await create_budget(world, amount=BIG)
    url = f"{world.project_url}/budgets/{budget.id}"
    async with world.sessions() as writer:  # another write of the organization in flight
        await writer.execute(
            text("SELECT id FROM organizations WHERE id = :id FOR NO KEY UPDATE"),
            {"id": world.workspace.org_id},
        )
        async with asyncio.timeout(5):
            response = await world.workspace.owner.patch(url, json={"amount_usd": "0.005"})
    assert response.status_code == 503, response.text
    assert response.json()["code"] == "BUDGET_BUSY"
    response = await world.workspace.owner.patch(url, json={"amount_usd": "0.005"})
    assert response.status_code == 200, response.text


async def test_a_budget_write_does_not_retry_a_statement_timeout(
    budget_world: BudgetWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only lock waits are retried: a statement that outlived the statement timeout is a slow
    query, not a busy budget, so it is tried once and answers the usual `503`."""
    world = budget_world
    budget = await create_budget(world, amount=BIG)
    calls = 0

    async def slow(*_: object, **__: object) -> None:
        nonlocal calls
        calls += 1
        raise OperationalError("UPDATE budgets", {}, psycopg.errors.QueryCanceled())

    monkeypatch.setattr(budgets_router.service, "update_budget", slow)
    response = await world.workspace.owner.patch(
        f"{world.project_url}/budgets/{budget.id}", json={"amount_usd": "0.005"}
    )
    assert response.status_code == 503, response.text
    assert response.json()["code"] == "SERVICE_UNAVAILABLE"
    assert calls == 1
