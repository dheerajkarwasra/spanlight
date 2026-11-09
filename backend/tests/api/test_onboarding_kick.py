"""The onboarding read queues a new project's first page fill once (live dashboards §4.9)."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Job
from tests.helpers import (
    Browser,
    Workspace,
    bearer,
    create_workspace,
    new_span_id,
    new_trace_id,
    span,
)


@pytest.fixture
async def workspace(browser_factory: Any) -> Workspace:
    owner: Browser = await browser_factory("owner@example.com")
    return await create_workspace(owner)


async def _kick_jobs(
    session_factory: async_sessionmaker[AsyncSession],
) -> list[tuple[str, str | None]]:
    async with session_factory() as db:
        rows = await db.execute(
            select(Job.kind, Job.dedupe_key)
            .where(Job.dedupe_key.like("first-traces:%"))
            .order_by(Job.kind)
        )
        return [(kind, key) for kind, key in rows.all()]


async def test_onboarding_with_traces_enqueues_each_job_once(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    response = await client.post(
        "/v1/traces",
        json={"spans": [span(trace_id=new_trace_id(), span_id=new_span_id())]},
        headers=bearer(workspace.api_key),
    )
    assert response.json()["rejected"] == [], response.text
    url = f"/api/v1/projects/{workspace.project_id}/onboarding"

    first, second = await asyncio.gather(workspace.owner.get(url), workspace.owner.get(url))

    assert first.json()["has_traces"] is True and second.json()["has_traces"] is True
    project = workspace.project_id
    assert await _kick_jobs(session_factory) == [
        (kind, f"first-traces:{kind}:{project}")
        for kind in sorted(
            ("rollup_hourly", "refresh_user_stats", "refresh_attribution", "rollup_cost_daily")
        )
    ]


async def test_onboarding_without_traces_enqueues_nothing(
    workspace: Workspace, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    response = await workspace.owner.get(f"/api/v1/projects/{workspace.project_id}/onboarding")

    assert response.json() == {"has_traces": False, "first_trace_at": None}
    assert await _kick_jobs(session_factory) == []


async def test_onboarding_with_only_old_traces_enqueues_nothing(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # After the 7-day job cleanup a project with old traces must not re-queue the jobs.
    old = datetime.now(UTC) - timedelta(hours=2)
    response = await client.post(
        "/v1/traces",
        json={"spans": [span(trace_id=new_trace_id(), span_id=new_span_id(), start=old)]},
        headers=bearer(workspace.api_key),
    )
    assert response.json()["rejected"] == [], response.text

    response = await workspace.owner.get(f"/api/v1/projects/{workspace.project_id}/onboarding")

    assert response.json()["has_traces"] is True
    assert await _kick_jobs(session_factory) == []
