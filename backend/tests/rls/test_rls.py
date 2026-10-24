"""Row-level security: telemetry is invisible across projects at the database level."""

from typing import Any

import httpx
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Span, Trace
from app.db.rls import bind_project, bypass_rls
from tests.helpers import Browser, bearer, create_workspace, new_trace_id, span


@pytest.fixture
async def two_tenants(browser_factory: Any, client: httpx.AsyncClient) -> tuple[Any, Any, str, str]:
    alice: Browser = await browser_factory("alice@example.com")
    bob: Browser = await browser_factory("bob@example.com")
    alice_ws = await create_workspace(alice, org_name="Alice Co")
    bob_ws = await create_workspace(bob, org_name="Bob Co")
    alice_trace, bob_trace = new_trace_id(), new_trace_id()
    for workspace, trace_id in ((alice_ws, alice_trace), (bob_ws, bob_trace)):
        response = await client.post(
            "/v1/traces",
            json={"spans": [span(trace_id=trace_id)]},
            headers=bearer(workspace.api_key),
        )
        assert response.json()["accepted"] == 1
    return alice_ws, bob_ws, alice_trace, bob_trace


async def test_app_role_is_not_a_superuser(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as db:
        is_super = await db.scalar(
            text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user")
        )
    assert is_super is False  # otherwise the RLS tests below would prove nothing


async def test_queries_without_project_binding_see_nothing(
    two_tenants: Any, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_factory() as db:
        assert await db.scalar(select(func.count()).select_from(Trace)) == 0
        assert await db.scalar(select(func.count()).select_from(Span)) == 0


async def test_bound_project_sees_only_its_rows(
    two_tenants: Any, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    alice_ws, _, alice_trace, bob_trace = two_tenants
    async with session_factory() as db:
        await bind_project(db, alice_ws.project_id)
        # No WHERE clause at all: the policy alone filters the rows.
        visible = (await db.scalars(select(Trace.trace_id))).all()
        bob_rows = await db.scalar(
            select(func.count()).select_from(Span).where(Span.trace_id == bob_trace)
        )
    assert visible == [alice_trace]
    assert bob_rows == 0


async def test_binding_does_not_leak_to_next_transaction(
    two_tenants: Any, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    alice_ws, *_ = two_tenants
    async with session_factory() as db:
        await bind_project(db, alice_ws.project_id)
        await db.commit()
        assert await db.scalar(select(func.count()).select_from(Trace)) == 0


async def test_cannot_insert_into_another_project(
    two_tenants: Any, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    alice_ws, bob_ws, *_ = two_tenants
    async with session_factory() as db:
        await bind_project(db, alice_ws.project_id)
        with pytest.raises(DBAPIError, match="row-level security"):
            await db.execute(
                text(
                    "INSERT INTO traces (project_id, trace_id, started_at, ended_at) "
                    "VALUES (:project_id, :trace_id, now(), now())"
                ),
                {"project_id": bob_ws.project_id, "trace_id": new_trace_id()},
            )


async def test_worker_bypass_sees_all_projects(
    two_tenants: Any, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_factory() as db:
        await bypass_rls(db)
        assert await db.scalar(select(func.count()).select_from(Trace)) == 2


async def test_api_cannot_read_other_tenants_trace(two_tenants: Any) -> None:
    alice_ws, bob_ws, _, bob_trace = two_tenants
    alice = alice_ws.owner
    # Alice's own project, Bob's trace id: filtered out.
    response = await alice.get(f"/api/v1/projects/{alice_ws.project_id}/traces/{bob_trace}")
    assert response.status_code == 404
    # Bob's project: Alice is not a member.
    response = await alice.get(f"/api/v1/projects/{bob_ws.project_id}/traces/{bob_trace}")
    assert response.status_code == 404
    listed = (await alice.get(f"/api/v1/projects/{alice_ws.project_id}/traces")).json()["items"]
    assert bob_trace not in {item["trace_id"] for item in listed}


async def test_trace_histogram_counts_only_own_project(two_tenants: Any) -> None:
    alice_ws, bob_ws, _, _ = two_tenants
    alice = alice_ws.owner
    # Each tenant holds one trace: Alice's histogram counts hers, never Bob's.
    own = await alice.get(f"/api/v1/projects/{alice_ws.project_id}/traces/histogram")
    assert own.status_code == 200
    assert sum(bucket["traces"] for bucket in own.json()["buckets"]) == 1
    other = await alice.get(f"/api/v1/projects/{bob_ws.project_id}/traces/histogram")
    assert other.status_code == 404
