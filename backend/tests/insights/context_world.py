"""The detector-context world the context tests seed: four projects stored through ingestion.

`quiet` has three calls; `busy` fires five detectors and also holds a tool loop older than the
tool window; `other` fires one detector of its own; `baselined` has enough history for 7-day
baselines and a recent hour far above them. Spans are pinned to `NOW`.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Project
from app.db.rls import bind_project, bypass_rls
from app.ingest.pipeline import IngestTarget, ingest_spans
from app.rollups.compute import backfill_rollups
from tests.helpers import new_trace_id, span
from tests.rls.factories import create_project

Sessions = async_sessionmaker[AsyncSession]

NOW = datetime.now(UTC).replace(microsecond=0)
RATE_LIMITED = "429 rate_limit_error: Number of request tokens has exceeded your rate limit"


def _llm(age: timedelta, **overrides: Any) -> dict[str, Any]:
    return span(trace_id=new_trace_id(), start=NOW - age, **overrides)


def _tool(trace_id: str, age: timedelta, query: str) -> dict[str, Any]:
    return span(
        trace_id=trace_id,
        start=NOW - age,
        name="lookup",
        kind="tool",
        provider=None,
        model=None,
        usage=None,
        input={"query": query},
        output={"hits": 0},
    )


def _tool_loop(age: timedelta, repeats: int) -> list[dict[str, Any]]:
    trace_id = new_trace_id()
    return [_tool(trace_id, age - timedelta(seconds=i), "same arguments") for i in range(repeats)]


def quiet_spans() -> list[dict[str, Any]]:
    return [_llm(timedelta(hours=2)) for _ in range(3)]


def busy_spans() -> list[dict[str, Any]]:
    """Fires `unpriced_spend`, `rate_limit_pressure`, `error_spike` (no baseline),
    `truncated_outputs` and `tool_loop`; also holds a tool loop older than the tool window."""
    unpriced = [_llm(timedelta(hours=5), provider="acme", model="mystery-model") for _ in range(25)]
    limited = [
        _llm(
            timedelta(minutes=5),
            provider="anthropic",
            model="claude-sonnet-4-5",
            status="error" if i < 20 else "ok",
            status_message=RATE_LIMITED if i < 20 else None,
        )
        for i in range(60)
    ]
    truncated = [
        _llm(timedelta(minutes=30), finish_reason="length" if i < 10 else "stop") for i in range(60)
    ]
    return (
        unpriced
        + limited
        + truncated
        + _tool_loop(timedelta(minutes=20), 5)
        + _tool_loop(timedelta(hours=3), 12)
    )


def other_spans() -> list[dict[str, Any]]:
    return [_llm(timedelta(hours=1), provider="acme", model="other-model") for _ in range(25)]


def baselined_spans() -> list[dict[str, Any]]:
    """Enough history two to four days back for 7-day baselines, then a recent hour whose cost
    is many times the baseline."""
    history = [
        _llm(
            timedelta(days=2, hours=i % 48),
            duration_ms=200.0,
            usage={"input_tokens": 100, "output_tokens": 10, "cached_tokens": 0},
        )
        for i in range(150)
    ]
    recent = [
        _llm(
            timedelta(minutes=10),
            model="gpt-4o",
            duration_ms=4000.0,
            usage={"input_tokens": 50_000, "output_tokens": 5_000, "cached_tokens": 0},
        )
        for _ in range(60)
    ]
    return history + recent


WORLD = {
    "quiet": quiet_spans,
    "busy": busy_spans,
    "other": other_spans,
    "baselined": baselined_spans,
}


async def store(sessions: Sessions, spans: list[dict[str, Any]], *, rollups: bool) -> uuid.UUID:
    async with sessions() as db, db.begin():
        project_id = await create_project(db)
    async with sessions() as db, db.begin():
        await bind_project(db, project_id)
        org_id = await db.scalar(select(Project.org_id).where(Project.id == project_id))
        assert org_id is not None
        target = IngestTarget(project_id=project_id, capture_payloads=True, org_id=org_id)
        outcome = await ingest_spans(db, target, spans, now=NOW)
    assert (outcome.accepted, outcome.rejected) == (len(spans), [])
    if rollups:
        async with sessions() as db:
            await bypass_rls(db)
            await backfill_rollups(db, project_id, NOW - timedelta(days=9), NOW)
            await db.commit()
    return project_id


async def seed_world(sessions: Sessions) -> dict[str, uuid.UUID]:
    return {
        name: await store(sessions, build(), rollups=name == "baselined")
        for name, build in WORLD.items()
    }
