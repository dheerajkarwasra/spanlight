"""Helpers shared by the ingestion tests."""

import secrets
import time
from typing import Any

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Span
from app.db.rls import bypass_rls
from tests.helpers import Browser, Workspace, bearer, create_api_key, create_project

Rows = list[dict[str, Any]]


async def ingest_native(
    client: httpx.AsyncClient, workspace: Workspace, spans: list[Any]
) -> httpx.Response:
    return await client.post("/v1/traces", json={"spans": spans}, headers=bearer(workspace.api_key))


async def workspace_for_project(owner: Browser, org_id: str, project_id: str) -> Workspace:
    """A `Workspace` for an existing project, with a fresh native API key."""
    key = await create_api_key(owner, project_id)
    return Workspace(owner, org_id, project_id, key["secret"])


async def another_project(workspace: Workspace, name: str) -> Workspace:
    """A second project in the workspace's organization, with its own API key."""
    project_id = await create_project(workspace.owner, workspace.org_id, name)
    return await workspace_for_project(workspace.owner, workspace.org_id, project_id)


async def stored_spans(session_factory: async_sessionmaker[AsyncSession]) -> list[Span]:
    """Every stored span, read past row-level security."""
    async with session_factory() as db:
        await bypass_rls(db)
        return list((await db.scalars(select(Span).order_by(Span.started_at))).all())


def nested_json(depth: int) -> str:
    """JSON text for `depth` arrays nested in each other."""
    return "[" * depth + "]" * depth


def otlp_json_span(attributes: dict[str, str] | None = None, *, name: str = "ok") -> dict[str, Any]:
    """One OTLP/JSON span with fresh ids that ended just now; `attributes` are string values."""
    now = time.time_ns()
    span: dict[str, Any] = {
        "traceId": secrets.token_hex(16),
        "spanId": secrets.token_hex(8),
        "name": name,
        "startTimeUnixNano": str(now - 1_000_000),
        "endTimeUnixNano": str(now),
    }
    if attributes:
        span["attributes"] = [
            {"key": key, "value": {"stringValue": value}} for key, value in attributes.items()
        ]
    return span


def otlp_json_document(*spans: dict[str, Any]) -> dict[str, Any]:
    """An OTLP/JSON export request holding `spans` in one scope."""
    return {"resourceSpans": [{"scopeSpans": [{"spans": list(spans)}]}]}


_SPAN_ROWS = text(
    "SELECT to_jsonb(s) - 'project_id' - 'ingested_at' FROM spans AS s "
    "WHERE project_id = :project_id ORDER BY trace_id, span_id"
)
_TRACE_ROWS = text(
    "SELECT to_jsonb(t) - 'project_id' FROM traces AS t "
    "WHERE project_id = :project_id ORDER BY trace_id"
)


async def telemetry_rows(
    session_factory: async_sessionmaker[AsyncSession], project_id: str
) -> tuple[Rows, Rows]:
    """A project's span rows and trace rows, every column but the project and ingest time."""
    async with session_factory() as db:
        await bypass_rls(db)
        spans = (await db.execute(_SPAN_ROWS, {"project_id": project_id})).scalars().all()
        traces = (await db.execute(_TRACE_ROWS, {"project_id": project_id})).scalars().all()
    return list(spans), list(traces)
