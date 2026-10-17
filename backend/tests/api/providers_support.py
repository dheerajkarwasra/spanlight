"""Seeding for the provider health and telemetry completeness API tests.

`Call` describes one span; `seed_trace` writes one trace holding some calls, with the trace's
stored retry figures chosen by the test (they are what ingestion's recompute writes, and the API
only reads them). `seed_incident` writes a status page incident, `seed_loss` one hourly loss row
and `compute` rebuilds a project's rollups over a range the way the rollup job does. Everything
is written past row-level security, as the worker writes it.

`pinned_app` starts the app on a frozen clock, so the rollup/raw split a route reads at is the
one the test computed, whatever the wall clock does meanwhile.
"""

import json
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.rls import bypass_rls
from app.main import create_app
from app.rollups.compute import compute_rollups, floor_hour

SessionFactory = async_sessionmaker[AsyncSession]


def pinned_now() -> datetime:
    """Half past the previous hour: the split (`floor_hour(now) - 1 h`) is stable for the test.

    It is never ahead of the wall clock. A route's default window (no `from`/`to`) ends at the
    wall clock, not the app clock, so a pinned time ahead of it (half past the current hour, in
    the first half of the hour) would leave spans seeded just before it out of every default
    window.
    """
    return floor_hour(datetime.now(UTC)) - timedelta(minutes=30)


def window(start: datetime, end: datetime) -> dict[str, str]:
    """`from`/`to` query parameters."""
    return {"from": start.isoformat(), "to": end.isoformat()}


def health_path(project_id: uuid.UUID, route: str = "health") -> str:
    return f"/api/v1/projects/{project_id}/providers/{route}"


def z(moment: datetime) -> str:
    """A UTC time as the API writes it."""
    return moment.isoformat().replace("+00:00", "Z")


@asynccontextmanager
async def pinned_app(settings: Settings, now: datetime) -> AsyncIterator[Any]:
    clock: Callable[[], datetime] = lambda: now  # noqa: E731 - a frozen clock
    application = create_app(settings, clock=clock)
    async with application.router.lifespan_context(application):
        yield application


_INSERT_TRACE = text(
    "INSERT INTO traces (project_id, trace_id, started_at, ended_at, environment, retry_calls, "
    "retry_cost_usd) VALUES (:project_id, :trace_id, :started_at, :ended_at, :environment, "
    ":retry_calls, :retry_cost_usd)"
)
_INSERT_SPAN = text(
    """
    INSERT INTO spans (project_id, trace_id, span_id, kind, name, status, status_message,
                       started_at, ended_at, provider, model, error_class, rate_limit_kind,
                       provider_error_type, provider_error_code, attributes)
    VALUES (:project_id, :trace_id, :span_id, :kind, 'call', :status, :status_message,
            :started_at, :ended_at, :provider, :model, :error_class, :rate_limit_kind,
            :provider_error_type, :provider_error_code, CAST(:attributes AS jsonb))
    """
)


@dataclass(frozen=True, slots=True)
class Call:
    """One span. The default is a successful openai llm call; an error class makes it failed."""

    started_at: datetime
    provider: str | None = "openai"
    error_class: str | None = None
    rate_limit_kind: str | None = None
    kind: str = "llm"
    model: str | None = "gpt-4.1"
    status_message: str | None = None
    provider_error_type: str | None = None
    provider_error_code: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    span_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])

    @property
    def status(self) -> str:
        return "ok" if self.error_class is None else "error"


def failed(at: datetime, error_class: str, kind: str | None = None, **extra: Any) -> Call:
    return Call(at, error_class=error_class, rate_limit_kind=kind, **extra)


async def seed_trace(
    sessions: SessionFactory,
    project_id: uuid.UUID,
    calls: list[Call],
    *,
    environment: str | None = "production",
    retry_calls: int | None = 0,
    retry_cost_usd: Decimal | None = Decimal(0),
) -> str:
    """One trace holding `calls`, started at the first call; returns its id."""
    trace_id = uuid.uuid4().hex
    started_at = min(call.started_at for call in calls)
    ended_at = max(call.started_at for call in calls) + timedelta(seconds=1)
    trace = {
        "project_id": project_id,
        "trace_id": trace_id,
        "started_at": started_at,
        "ended_at": ended_at,
        "environment": environment,
        "retry_calls": retry_calls,
        "retry_cost_usd": retry_cost_usd,
    }
    spans = [
        {
            "project_id": project_id,
            "trace_id": trace_id,
            "span_id": call.span_id,
            "kind": call.kind,
            "status": call.status,
            "status_message": call.status_message,
            "started_at": call.started_at,
            "ended_at": call.started_at + timedelta(seconds=1),
            "provider": call.provider,
            "model": call.model,
            "error_class": call.error_class,
            "rate_limit_kind": call.rate_limit_kind,
            "provider_error_type": call.provider_error_type,
            "provider_error_code": call.provider_error_code,
            "attributes": json.dumps(call.attributes),
        }
        for call in calls
    ]
    async with sessions() as db:
        await bypass_rls(db)
        await db.execute(_INSERT_TRACE, trace)
        await db.execute(_INSERT_SPAN, spans)
        await db.commit()
    return trace_id


async def seed_calls(
    sessions: SessionFactory, project_id: uuid.UUID, calls: list[Call], **trace: Any
) -> None:
    """Each call in a trace of its own."""
    for call in calls:
        await seed_trace(sessions, project_id, [call], **trace)


async def compute(
    sessions: SessionFactory, project_id: uuid.UUID, start: datetime, end: datetime
) -> None:
    async with sessions() as db:
        await compute_rollups(db, project_id, start, end)
        await db.commit()


async def seed_incident(
    sessions: SessionFactory,
    external_id: str,
    started_at: datetime,
    *,
    resolved_at: datetime | None = None,
    provider: str = "openai",
    api_affected: bool = True,
    title: str = "Elevated errors",
) -> str:
    incident_id = uuid.uuid4()
    async with sessions() as db:
        await db.execute(
            text(
                "INSERT INTO provider_incidents (id, provider, source, external_id, title, "
                "impact, status, api_affected, started_at, resolved_at, url, fetched_at) "
                "VALUES (:id, :provider, 'statuspage_json', :external_id, :title, 'major', "
                ":status, :api_affected, :started_at, :resolved_at, "
                "'https://status.example.com/incidents/' || :external_id, :started_at)"
            ),
            {
                "id": incident_id,
                "provider": provider,
                "external_id": external_id,
                "title": title,
                "status": "investigating" if resolved_at is None else "resolved",
                "api_affected": api_affected,
                "started_at": started_at,
                "resolved_at": resolved_at,
            },
        )
        await db.commit()
    return str(incident_id)


async def seed_loss(
    sessions: SessionFactory,
    project_id: uuid.UUID,
    bucket_start: datetime,
    source: str,
    reason: str,
    count: int,
) -> None:
    async with sessions() as db:
        await bypass_rls(db)
        await db.execute(
            text(
                "INSERT INTO telemetry_losses_hourly (project_id, bucket_start, source, reason, "
                "count) VALUES (:project_id, :bucket_start, :source, :reason, :count)"
            ),
            {
                "project_id": project_id,
                "bucket_start": bucket_start,
                "source": source,
                "reason": reason,
                "count": count,
            },
        )
        await db.commit()
