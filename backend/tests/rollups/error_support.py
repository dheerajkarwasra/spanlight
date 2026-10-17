"""Seeding for the error rollup tests: traces with one span each, every dimension chosen.

`FailedSpan` describes one span; `seed_spans` writes a list of them (one trace per span, past
row-level security) and `compute` rebuilds a project's rollups over a range the way the job does.
"""

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text

from app.db.rls import bypass_rls
from app.rollups.compute import compute_rollups
from tests.jobs.bypass_support import SessionFactory

_INSERT_TRACE = text(
    "INSERT INTO traces (project_id, trace_id, started_at, ended_at, environment) "
    "VALUES (:project_id, :trace_id, :started_at, :ended_at, :environment)"
)
_INSERT_SPAN = text(
    """
    INSERT INTO spans (project_id, trace_id, span_id, kind, name, status, started_at, ended_at,
                       provider, model, error_class, rate_limit_kind, attributes)
    VALUES (:project_id, :trace_id, :span_id, :kind, 'call', :status, :started_at, :ended_at,
            :provider, :model, :error_class, :rate_limit_kind, CAST(:attributes AS jsonb))
    """
)


@dataclass(frozen=True, slots=True)
class FailedSpan:
    """One span to seed. The defaults are a failed llm span with no rate-limit kind."""

    started_at: datetime
    status: str = "error"
    kind: str = "llm"
    provider: str | None = "openai"
    model: str | None = "gpt-4.1"
    environment: str | None = "production"
    error_class: str | None = "provider_5xx"
    rate_limit_kind: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)


async def seed_spans(
    session_factory: SessionFactory, project_id: uuid.UUID, spans: list[FailedSpan]
) -> list[str]:
    """Write each span in its own trace and return the trace ids, in order."""
    trace_ids = [uuid.uuid4().hex for _ in spans]
    traces: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    for trace_id, span in zip(trace_ids, spans, strict=True):
        ended_at = span.started_at + timedelta(seconds=1)
        common = {"project_id": project_id, "trace_id": trace_id, "started_at": span.started_at}
        traces.append({**common, "ended_at": ended_at, "environment": span.environment})
        rows.append(
            {
                **common,
                "ended_at": ended_at,
                "span_id": trace_id[:16],
                "kind": span.kind,
                "status": span.status,
                "provider": span.provider,
                "model": span.model,
                "error_class": span.error_class,
                "rate_limit_kind": span.rate_limit_kind,
                "attributes": json.dumps(span.attributes),
            }
        )
    async with session_factory() as db:
        await bypass_rls(db)
        await db.execute(_INSERT_TRACE, traces)
        await db.execute(_INSERT_SPAN, rows)
        await db.commit()
    return trace_ids


async def compute(
    session_factory: SessionFactory, project_id: uuid.UUID, start: datetime, end: datetime
) -> int:
    async with session_factory() as db:
        written = await compute_rollups(db, project_id, start, end)
        await db.commit()
    return written
