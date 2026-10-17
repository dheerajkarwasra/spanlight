"""Helpers for the billing fetch job tests: sources made by the service, jobs claimed for real.

- `owner_and_project(browser_factory)`: a signed-up owner with an organization and a project.
- `add_source(...)`: a billing source created through `app.reconciliation.service`, its key
  sealed under the test keyring; no provider call is made (the check is off while creating).
- `serve(monkeypatch, settings, api)`: the job's HTTP client answers from the scripted `api`.
- `anthropic_api(...)`: the Anthropic cost and usage fixtures as one page each.
- `claimed_context(...)`: a child (or parent) job enqueued and claimed, as the worker hands it.
- `run_child(...)` runs the child job through a `Worker`, as production does, and returns the
  job row; `lines`, `runs` and `source_row` read back what it wrote.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.schemas.billing_sources import BillingSourceCreate
from app.config import Settings
from app.db.models import Job
from app.db.models.billing import BillingSource
from app.jobs import queue
from app.jobs.context import TaskContext
from app.jobs.tasks import TASKS
from app.jobs.worker import Worker
from app.reconciliation import fetch_jobs, service
from app.reconciliation.runs import FETCH_SOURCE_JOB, child_payload
from tests.helpers import create_workspace
from tests.reconciliation.fetch_support import (
    ANTHROPIC_COST_PATH,
    ANTHROPIC_KEY,
    ANTHROPIC_USAGE_PATH,
    DAY_FROM,
    DAY_TO,
    OPENAI_KEY,
    Reply,
    ScriptedApi,
    fixture_page,
    page,
    single_page,
    vetted_client,
)

SessionFactory = async_sessionmaker[AsyncSession]
NOW = datetime(2026, 10, 16, 4, 0, tzinfo=UTC)
TODAY = date(2026, 10, 16)
KEYS = {"anthropic_admin_api": ANTHROPIC_KEY, "openai_costs_api": OPENAI_KEY}


@dataclass(frozen=True)
class Owner:
    user_id: uuid.UUID
    org_id: uuid.UUID
    project_id: uuid.UUID


async def owner_and_project(browser_factory: Any, email: str = "owner@example.com") -> Owner:
    owner = await browser_factory(email)
    workspace = await create_workspace(owner)
    return Owner(
        user_id=uuid.UUID(owner.user["id"]),
        org_id=uuid.UUID(workspace.org_id),
        project_id=uuid.UUID(workspace.project_id),
    )


async def add_source(
    session_factory: SessionFactory,
    settings: Settings,
    owner: Owner,
    *,
    kind: str = "anthropic_admin_api",
    name: str = "Prod account",
    projects: list[uuid.UUID] | None = None,
    provider: str | None = None,
) -> uuid.UUID:
    """A source created by the service; created with provider calls off, so `never_fetched`."""
    quiet = settings.model_copy(update={"billing_sources_enabled": False})
    data = BillingSourceCreate.model_validate(
        {
            "kind": kind,
            "name": name,
            "provider": provider,
            "admin_key": SecretStr(KEYS[kind]) if kind in KEYS else None,
            "project_ids": projects if projects is not None else [owner.project_id],
        }
    )
    async with session_factory() as db, httpx.AsyncClient() as unused:
        source = await service.create_source(
            db, owner.org_id, owner.user_id, data, settings=quiet, http=unused, now=NOW
        )
        await db.commit()
    return source.id


def serve(monkeypatch: pytest.MonkeyPatch, api: ScriptedApi) -> None:
    """The fetch job's client is the vetted client over `api`."""

    def build(settings: Settings) -> httpx.AsyncClient:
        return vetted_client(settings, api)

    monkeypatch.setattr(fetch_jobs, "build_http_client", build)


def anthropic_api(cost: dict[str, Any] | None = None) -> ScriptedApi:
    """The Anthropic fixtures, one page each; `cost` replaces the cost page."""
    return ScriptedApi(
        routes={
            ANTHROPIC_COST_PATH: [Reply(json=cost)]
            if cost
            else single_page("anthropic_cost_page.json"),
            ANTHROPIC_USAGE_PATH: single_page("anthropic_usage_page.json"),
        }
    )


def failing_api(
    status: int, headers: dict[str, str] | None = None, body: bytes = b""
) -> ScriptedApi:
    """A provider that answers every request with `status`."""
    return ScriptedApi(default=Reply(status=status, headers=headers or {}, body=body))


def cost_page_with(**changes: Any) -> dict[str, Any]:
    """The Anthropic cost fixture as one page, the first result of the first bucket changed."""
    buckets = fixture_page("anthropic_cost_page.json")["data"]
    buckets[0]["results"][0].update(changes)
    return page(buckets)


def payload(
    source_id: uuid.UUID,
    day_from: date = DAY_FROM,
    day_to: date = DAY_TO,
    *,
    trigger: str = "scheduled",
    run_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    return child_payload(source_id, day_from, day_to, trigger=trigger, run_id=run_id)


async def enqueue_child(
    session_factory: SessionFactory, body: dict[str, Any], *, max_attempts: int = 3
) -> None:
    async with session_factory() as db:
        assert await queue.enqueue(
            db, FETCH_SOURCE_JOB, body, dedupe_key=f"test:{uuid.uuid4()}", max_attempts=max_attempts
        )
        await db.commit()


async def claimed_context(
    session_factory: SessionFactory,
    settings: Settings,
    kind: str,
    body: dict[str, Any] | None = None,
    *,
    now: datetime = NOW,
) -> TaskContext:
    """A job of `kind` enqueued and claimed by id (its heartbeat needs a real lease), whatever
    else is queued, with the clock at `now`."""
    async with session_factory() as db:
        await queue.enqueue(db, kind, body or {}, dedupe_key=f"test:{uuid.uuid4()}")
        row = (await db.execute(_CLAIM_LATEST, {"kind": kind})).one()
        await db.commit()
    job = queue.ClaimedJob(
        id=row.id,
        kind=row.kind,
        payload=row.payload,
        attempts=row.attempts,
        max_attempts=row.max_attempts,
        fence=row.fence,
    )
    return TaskContext(
        job=job, settings=settings, session_factory=session_factory, clock=lambda: now
    )


_CLAIM_LATEST = text(
    """
    UPDATE jobs
    SET status = 'running', attempts = attempts + 1, fence = fence + 1,
        lease_until = now() + interval '60 seconds'
    WHERE id = (SELECT max(id) FROM jobs WHERE kind = :kind)
    RETURNING id, kind, payload, attempts, max_attempts, fence
    """
)


async def run_child(
    session_factory: SessionFactory, settings: Settings, body: dict[str, Any]
) -> Job:
    """Enqueue the child job, let a worker run it once, and return its row."""
    await enqueue_child(session_factory, body)
    worker = Worker(session_factory, settings, handlers=TASKS)
    assert await worker.run_once()
    return await latest_job(session_factory)


async def rerun_now(session_factory: SessionFactory, settings: Settings) -> Job:
    """Make the queued child runnable at once and let a worker run it again."""
    async with session_factory() as db:
        await db.execute(text("UPDATE jobs SET run_after = now() WHERE status = 'queued'"))
        await db.commit()
    assert await Worker(session_factory, settings, handlers=TASKS).run_once()
    return await latest_job(session_factory)


async def latest_job(session_factory: SessionFactory) -> Job:
    async with session_factory() as db:
        job = await db.scalar(
            select(Job).where(Job.kind == FETCH_SOURCE_JOB).order_by(Job.id.desc()).limit(1)
        )
    assert job is not None
    return job


async def rows(session_factory: SessionFactory, sql: str, **params: Any) -> list[dict[str, Any]]:
    async with session_factory() as db:
        return [dict(row) for row in (await db.execute(text(sql), params)).mappings()]


async def lines(session_factory: SessionFactory, source_id: uuid.UUID) -> list[dict[str, Any]]:
    return await rows(
        session_factory,
        "SELECT * FROM provider_cost_lines WHERE source_id = :id ORDER BY day, line_key",
        id=source_id,
    )


async def runs(session_factory: SessionFactory, source_id: uuid.UUID) -> list[dict[str, Any]]:
    return await rows(
        session_factory,
        "SELECT * FROM billing_fetch_runs WHERE source_id = :id ORDER BY started_at, id",
        id=source_id,
    )


async def source_row(session_factory: SessionFactory, source_id: uuid.UUID) -> BillingSource:
    async with session_factory() as db:
        found = await db.scalar(select(BillingSource).where(BillingSource.id == source_id))
    assert found is not None
    return found
