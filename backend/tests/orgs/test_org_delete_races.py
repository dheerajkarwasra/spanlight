"""Lock order around an organization's deletion (`app.services.deletion`).

`delete_org` holds the organization row `FOR UPDATE`, then its projects `FOR UPDATE` in id order,
then cascades into everything under them. Every other writer that holds a row the cascade wants
and then needs the organization row (an audit event's foreign key) must take the organization
first, or the two wait on each other. Each test here forces one such interleaving on a real
Postgres: one side holds its first lock, the other starts and is seen waiting on a lock in
`pg_stat_activity`, the first side finishes, and both must complete without a deadlock.
"""

import asyncio
import time
import uuid
from collections.abc import AsyncIterator, Coroutine
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.budgets.jobs import reap_expired
from app.budgets.reserve import RESERVATION_LEASE, BlockBudget, reserve
from app.budgets.types import BudgetPeriod
from app.db.models import User
from app.db.rls import bind_project, bypass_rls
from app.services.deletion import delete_organization, lock_organization
from tests.conftest import BrowserFactory
from tests.helpers import (
    Browser,
    create_project,
    create_workspace,
    join_with_role,
    new_span_id,
    new_trace_id,
)

Sessions = async_sessionmaker[AsyncSession]

ME = "/api/v1/auth/me"
PASSWORD = "correct horse battery"  # what the browser factory signs users up with
WAIT_LIMIT_SECONDS = 10


async def _hold_organization(db: AsyncSession, org_id: uuid.UUID) -> None:
    """`delete_org` up to its first lock. A cycle from here surfaces after Postgres's default
    `deadlock_timeout` (one second; the app role may not lower it), well inside the waits below."""
    assert await lock_organization(db, org_id) is not None


async def _lock_waiters(sessions: Sessions) -> int:
    """How many backends of this database are blocked on a lock right now. A fresh transaction
    per look: `pg_stat_activity` is one snapshot per transaction."""
    async with sessions() as db:
        waiting = await db.scalar(
            text(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE wait_event_type = 'Lock' AND datname = current_database()"
            )
        )
    return int(waiting or 0)


@asynccontextmanager
async def _waiting[T](
    sessions: Sessions, coro: Coroutine[Any, Any, T], *, waiters: int = 1
) -> AsyncIterator[asyncio.Task[T]]:
    """Start `coro` and yield its task once `waiters` backends are blocked on a lock, so the
    side started here is known to be waiting, not merely slow. A task still pending on exit (an
    earlier assertion failed) is cancelled and awaited, so no pending task outlives the test."""
    task = asyncio.create_task(coro)
    try:
        deadline = time.monotonic() + WAIT_LIMIT_SECONDS
        while await _lock_waiters(sessions) < waiters:
            if task.done():
                pytest.fail(f"the side finished without waiting: {task.result()!r}")
            if time.monotonic() > deadline:
                pytest.fail("nothing waited for a lock: the two sides do not meet")
            await asyncio.sleep(0.01)
        yield task
    finally:
        if not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


@pytest.mark.serial
@pytest.mark.parametrize("edit", ["role", "remove"])
async def test_member_edit_and_org_delete_both_complete(
    browser_factory: BrowserFactory, session_factory: Sessions, edit: str
) -> None:
    """`delete_org` holds the organization; a member edit then holds the membership rows and
    its audit event waits for the organization. The deletion's cascade into the memberships
    must not wait on those rows: the edit waits for the deletion and answers 404."""
    owner: Browser = await browser_factory("owner@example.com")
    member: Browser = await browser_factory("member@example.com")
    workspace = await create_workspace(owner)
    await join_with_role(owner, member, workspace.org_id, "member")
    org_id = uuid.UUID(workspace.org_id)
    url = f"/api/v1/orgs/{org_id}/members/{member.user['id']}"
    request = (
        owner.patch(url, json={"role": "admin", "password": "correct horse battery"})
        if edit == "role"
        else owner.delete(url, json={"password": PASSWORD})
    )
    async with session_factory() as deleting:
        await _hold_organization(deleting, org_id)
        async with _waiting(session_factory, request) as editing, asyncio.timeout(10):
            await delete_organization(deleting, org_id)
            await deleting.commit()
            response = await editing
    assert response.status_code == 404, response.text
    async with session_factory() as db:
        left = await db.scalar(
            text("SELECT count(*) FROM memberships WHERE org_id = :id"), {"id": org_id}
        )
    assert left == 0


@pytest.mark.serial
async def test_account_delete_and_org_delete_both_complete(
    browser_factory: BrowserFactory, session_factory: Sessions
) -> None:
    """`delete_org` holds the organization; an account deletion then holds the user's
    membership rows and its audit event in that organization waits for it. The deletion's
    cascade must not wait on those rows: the account goes once the organization is gone."""
    owner: Browser = await browser_factory("owner@example.com")
    leaver: Browser = await browser_factory("leaver@example.com")
    workspace = await create_workspace(owner)
    await join_with_role(owner, leaver, workspace.org_id, "member")
    org_id = uuid.UUID(workspace.org_id)
    request = leaver.delete(ME, json={"password": PASSWORD, "confirm": "delete"})
    async with session_factory() as deleting:
        await _hold_organization(deleting, org_id)
        async with _waiting(session_factory, request) as leaving, asyncio.timeout(10):
            await delete_organization(deleting, org_id)
            await deleting.commit()
            response = await leaving
    assert response.status_code == 204, response.text
    async with session_factory() as db:
        assert await db.get(User, uuid.UUID(leaver.user["id"])) is None


@pytest.mark.serial
async def test_accept_invite_and_org_delete_both_complete(
    browser_factory: BrowserFactory, session_factory: Sessions
) -> None:
    """`delete_org` holds the organization; an invitee accepting then holds the invite row,
    and the membership insert waits for the organization. The deletion's cascade into the
    invites must not wait on that row: the accept waits for the deletion and answers 404."""
    owner: Browser = await browser_factory("owner@example.com")
    invitee: Browser = await browser_factory("invitee@example.com")
    workspace = await create_workspace(owner)
    org_id = uuid.UUID(workspace.org_id)
    invite = await owner.post(
        f"/api/v1/orgs/{org_id}/invites",
        json={"password": "correct horse battery", "role": "member"},
    )
    assert invite.status_code == 201, invite.text
    token = invite.json()["url"].split("#token=", 1)[1]
    request = invitee.post("/api/v1/invites/accept", json={"token": token})
    async with session_factory() as deleting:
        await _hold_organization(deleting, org_id)
        async with _waiting(session_factory, request) as accepting, asyncio.timeout(10):
            await delete_organization(deleting, org_id)
            await deleting.commit()
            response = await accepting
    assert response.status_code == 404, response.text
    async with session_factory() as db:
        left = await db.scalar(
            text("SELECT count(*) FROM invites WHERE org_id = :id"), {"id": org_id}
        )
    assert left == 0


async def _org_with_expired_reservations(
    owner: Browser, session_factory: Sessions, now: datetime
) -> tuple[dict[str, Any], list[uuid.UUID]]:
    """An organization of three projects, each with a block budget and one held reservation
    whose lease ran out before `now`. Returns the org and the reservation ids in project order."""
    org = (await owner.post("/api/v1/orgs", json={"name": "Acme"})).json()
    project_ids = sorted(
        [uuid.UUID(await create_project(owner, org["id"], name)) for name in ("a", "b", "c")]
    )
    admitted = now - RESERVATION_LEASE - timedelta(minutes=1)
    reservation_ids = []
    for project_id in project_ids:
        budget = await owner.post(
            f"/api/v1/projects/{project_id}/budgets",
            json={
                "name": "cap",
                "scope": "project",
                "period": "daily",
                "amount_usd": "1000000",
                "action": "block",
            },
        )
        assert budget.status_code == 201, budget.text
        block = BlockBudget(
            uuid.UUID(budget.json()["id"]), "cap", BudgetPeriod.DAILY, Decimal("1000000")
        )
        async with session_factory() as db, db.begin():
            await bind_project(db, project_id)
            refusal = await reserve(
                db,
                project_id,
                [block],
                Decimal("0.0001"),
                trace_id=new_trace_id(),
                span_id=new_span_id(),
                now=admitted,
            )
            assert refusal is None
            reservation_ids.append(
                await db.scalar(
                    text("SELECT id FROM budget_reservations WHERE project_id = :p"),
                    {"p": project_id},
                )
            )
    return org, reservation_ids


@pytest.mark.serial
@pytest.mark.parametrize("first", ["reaper", "deleter"])
async def test_budget_reaper_and_org_delete_both_complete(
    browser_factory: BrowserFactory, session_factory: Sessions, first: str
) -> None:
    """The reaper holds the projects `KEY SHARE` in id order, then the reservation rows;
    `delete_org` holds the organization, then the projects `FOR UPDATE` in the same order, then
    cascades into the rows. Whichever arrives second waits at the first project, holding
    nothing the other wants. The test holds one reservation row to keep the first side
    mid-batch, with every project held, while the second arrives."""
    owner: Browser = await browser_factory("owner@example.com")
    now = datetime.now(UTC)
    org, reservation_ids = await _org_with_expired_reservations(owner, session_factory, now)
    # The reaper's update reaches the last row; the cascade reaches the first.
    held = reservation_ids[-1] if first == "reaper" else reservation_ids[0]
    reap: Coroutine[Any, Any, object] = reap_expired(session_factory, now=now)
    delete: Coroutine[Any, Any, object] = owner.delete(
        f"/api/v1/orgs/{org['id']}", json={"confirm": org["slug"], "password": PASSWORD}
    )
    one, two = (reap, delete) if first == "reaper" else (delete, reap)
    async with session_factory() as holding:
        await bypass_rls(holding)
        await holding.execute(
            text("SELECT id FROM budget_reservations WHERE id = :id FOR UPDATE"), {"id": held}
        )
        async with (
            _waiting(session_factory, one) as first_side,  # stopped at the held row
            _waiting(session_factory, two, waiters=2) as second_side,  # at the first project
        ):
            await holding.rollback()
            async with asyncio.timeout(10):
                results = await asyncio.gather(first_side, second_side)
    reaped, response = results if first == "reaper" else results[::-1]
    assert isinstance(response, httpx.Response) and isinstance(reaped, tuple)
    assert response.status_code == 204, response.text
    # Reaper first: it reaped all three before the cascade removed them. Deleter first: the
    # reaper found no project and no row left.
    assert reaped[0] == (3 if first == "reaper" else 0)


async def test_org_delete_takes_its_projects_sorted_before_the_delete(
    browser_factory: BrowserFactory, session_factory: Sessions
) -> None:
    """The race above cannot force an interleaving inside one statement; the order the deleter
    takes the projects in is what keeps it from crossing the reaper's sorted `KEY SHARE`."""
    owner: Browser = await browser_factory("owner@example.com")
    workspace = await create_workspace(owner)
    org_id = uuid.UUID(workspace.org_id)
    statements: list[str] = []

    def record(_conn: Any, _cursor: Any, statement: str, *_rest: Any) -> None:
        statements.append(statement)

    engine = session_factory.kw["bind"].sync_engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        async with session_factory() as db, db.begin():
            assert await lock_organization(db, org_id) is not None
            await delete_organization(db, org_id)
    finally:
        event.remove(engine, "before_cursor_execute", record)

    def first(*markers: str) -> int:
        found = (i for i, s in enumerate(statements) if all(m in s for m in markers))
        return next(found, len(statements))  # missing sorts last

    locked = first("FROM projects", "ORDER BY projects.id", "FOR UPDATE")
    deleted = first("DELETE FROM projects")
    assert locked < deleted < len(statements), "\n".join(statements)


@pytest.mark.serial
async def test_a_proof_route_waiting_on_the_org_lets_foreign_keys_to_its_user_through(
    browser_factory: BrowserFactory, session_factory: Sessions
) -> None:
    """The same person, two tabs: one deletes a project and holds their user row while it waits
    for the organization; the other holds the organization and inserts a row whose foreign key
    needs `KEY SHARE` on that user. Neither may wait on the other."""
    owner: Browser = await browser_factory("owner@example.com")
    workspace = await create_workspace(owner)
    org_id = uuid.UUID(workspace.org_id)
    url = f"/api/v1/projects/{workspace.project_id}"
    slug = (await owner.get(url)).json()["slug"]
    request = owner.delete(url, json={"confirm": slug, "password": PASSWORD})
    async with session_factory() as other_tab:
        await _hold_organization(other_tab, org_id)
        async with _waiting(session_factory, request) as deleting, asyncio.timeout(10):
            await other_tab.execute(
                text("SELECT 1 FROM users WHERE id = :id FOR KEY SHARE"), {"id": owner.user["id"]}
            )
            await other_tab.commit()
            response = await deleting
    assert response.status_code == 204, response.text
