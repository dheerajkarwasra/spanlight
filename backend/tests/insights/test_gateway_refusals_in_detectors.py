"""The Doctor judges providers and models, never the gateway key's own limiter.

A burst of key-limit refusals is stored as `rate_limit` spans of kind `gateway`: Spanlight's own
limiter answered, no provider was asked and no model failed, so `rate_limit_pressure` and
`error_spike` drop them before counting, like fault-injected spans (`provider_traffic`). A burst
of provider 429s still fires both. The bursts are stored through ingestion, so the class and the
kind are the ones ingestion decides.

Refusals are also left out of the detectors' span read itself, before its cap, so a refusal flood
cannot push real calls out of the context; and a refusal never makes its trace "unpriced": no
provider was called, so its cost is a known zero.
"""

import dataclasses
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Project, Trace
from app.db.rls import bind_project, bypass_rls
from app.ingest.pipeline import IngestTarget, ingest_spans
from app.insights import context_queries
from app.insights.context import ANY, Baselines, DetectorContext
from app.insights.context_queries import load_context
from app.insights.detectors import error_spike
from app.insights.detectors.base import Detector
from app.insights.detectors.rate_limit_pressure import MIN_CALLS, WINDOW, detector
from app.insights.schemas import Finding, Window
from tests.helpers import new_span_id, new_trace_id
from tests.rls.factories import create_project

Sessions = async_sessionmaker[AsyncSession]

NOW = datetime.now(UTC).replace(microsecond=0)
BURST = MIN_CALLS + 10


def _span(attributes: dict[str, Any], message: str, provider: str | None) -> dict[str, Any]:
    started = NOW - timedelta(minutes=5)
    return {
        "trace_id": new_trace_id(),
        "span_id": new_span_id(),
        "name": "chat_completions",
        "kind": "llm",
        "status": "error",
        "status_message": message,
        "start_time": started.isoformat(),
        "end_time": (started + timedelta(milliseconds=3)).isoformat(),
        "provider": provider,
        "model": None,
        "attributes": attributes,
        "trace": {"environment": "production"},
    }


def _key_limit_refusal() -> dict[str, Any]:
    """A key-limit refusal as the gateway records it (`app.gateway.refusal_span`)."""
    return _span(
        {"spanlight.gateway.status": 429, "spanlight.gateway.error_code": "RATE_LIMITED"},
        "429 RATE_LIMITED: Rate limit reached for gateway key abc: 60 requests per minute.",
        provider=None,
    )


def _provider_429() -> dict[str, Any]:
    """A request the provider refused for its own rate limit, passed through by the gateway."""
    return _span(
        {
            "spanlight.gateway.status": 429,
            "spanlight.gateway.upstream_status": 429,
            "provider.error.type": "rate_limit_error",
        },
        "429 rate_limit_error: Number of request tokens has exceeded your per-minute rate limit",
        provider="anthropic",
    )


async def _store(sessions: Sessions, spans: list[dict[str, Any]]) -> uuid.UUID:
    async with sessions() as db, db.begin():
        project_id = await create_project(db)
    async with sessions() as db, db.begin():
        await bind_project(db, project_id)
        org_id = await db.scalar(select(Project.org_id).where(Project.id == project_id))
        assert org_id is not None
        target = IngestTarget(project_id=project_id, capture_payloads=True, org_id=org_id)
        outcome = await ingest_spans(db, target, spans, now=NOW)
    assert (outcome.accepted, outcome.rejected) == (len(spans), [])
    return project_id


async def _context(sessions: Sessions, project_id: uuid.UUID) -> DetectorContext:
    async with sessions() as db:
        ctx = await load_context(db, project_id, NOW)
        await db.commit()
    assert ctx is not None
    return ctx.sliced(Window(start=NOW - WINDOW, end=NOW))


async def _findings(
    sessions: Sessions, project_id: uuid.UUID, run: Detector = detector
) -> list[Finding]:
    return run.run(await _context(sessions, project_id))


async def _error_spikes(sessions: Sessions, project_id: uuid.UUID) -> list[Finding]:
    """`error_spike` over the stored spans, against a 1 % error-rate baseline for the spans'
    slice (production, any provider, no model): no rollups exist for a fresh project."""
    ctx = await _context(sessions, project_id)
    baseline = Baselines({("error_rate", "production", ANY, None): Decimal("0.01")})
    return error_spike.detector.run(dataclasses.replace(ctx, baselines=baseline))


async def test_gateway_limiter_burst_does_not_fire_rate_limit_pressure(
    session_factory: Sessions,
) -> None:
    project_id = await _store(session_factory, [_key_limit_refusal() for _ in range(BURST)])

    assert await _findings(session_factory, project_id) == []


async def test_provider_429_burst_still_fires_rate_limit_pressure(
    session_factory: Sessions,
) -> None:
    project_id = await _store(session_factory, [_provider_429() for _ in range(BURST)])

    [finding] = await _findings(session_factory, project_id)

    assert finding.kind == "rate_limit_pressure"
    assert finding.evidence.metrics["rate_limited"] == BURST
    assert finding.params["provider"] == "anthropic"


async def test_gateway_refusals_do_not_dilute_a_provider_burst(
    session_factory: Sessions,
) -> None:
    # Dropped before counting, so they are not calls either: the share stays the provider's.
    spans = [_provider_429() for _ in range(BURST)]
    spans += [{**_key_limit_refusal(), "provider": "anthropic"} for _ in range(BURST * 10)]
    project_id = await _store(session_factory, spans)

    [finding] = await _findings(session_factory, project_id)

    assert (finding.evidence.metrics["rate_limited"], finding.evidence.metrics["calls"]) == (
        BURST,
        BURST,
    )


async def test_gateway_limiter_burst_does_not_open_an_error_spike(
    session_factory: Sessions,
) -> None:
    project_id = await _store(session_factory, [_key_limit_refusal() for _ in range(BURST)])

    assert await _error_spikes(session_factory, project_id) == []


async def test_provider_429_burst_still_opens_an_error_spike(
    session_factory: Sessions,
) -> None:
    project_id = await _store(session_factory, [_provider_429() for _ in range(BURST)])

    [finding] = await _error_spikes(session_factory, project_id)

    assert finding.kind == "error_spike"
    assert finding.evidence.metrics["errors"] == BURST


async def test_refusals_do_not_take_the_detector_span_cap(
    session_factory: Sessions, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(context_queries, "SPAN_CAP", 5)
    earlier = (NOW - timedelta(minutes=10)).isoformat()
    real = {**_provider_429(), "start_time": earlier, "end_time": earlier}
    # Newer than the real failure, and four times the cap: read first if they were read at all.
    project_id = await _store(session_factory, [real] + [_key_limit_refusal() for _ in range(20)])

    async with session_factory() as db:
        ctx = await load_context(db, project_id, NOW)
        await db.commit()

    assert ctx is not None
    assert [span.span_id for span in ctx.llm_spans] == [real["span_id"]]
    assert ctx.truncated is False


async def test_a_refusal_does_not_mark_its_trace_unpriced(session_factory: Sessions) -> None:
    refusal = _key_limit_refusal()
    # A failed provider call with a model and no usage has no known price.
    provider_call = {**_provider_429(), "model": "claude-sonnet-4-5"}
    project_id = await _store(session_factory, [refusal, provider_call])

    async with session_factory() as db:
        await bypass_rls(db)
        rows = await db.execute(
            select(Trace.trace_id, Trace.has_unpriced).where(Trace.project_id == project_id)
        )
        unpriced = {row.trace_id: row.has_unpriced for row in rows}

    assert unpriced == {refusal["trace_id"]: False, provider_call["trace_id"]: True}
