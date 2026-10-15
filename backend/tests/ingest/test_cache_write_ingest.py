"""Cache-write usage on the native API: stored, priced by TTL, checked per span, read back.

The priced case is the pilot's real Claude Haiku 4.5 call with a 6,404-token 1-hour cache write
(`docs/plans/reports/pilot-2026-10-14.md`, D1): Anthropic billed $0.012887. It is priced by the
2026-10-15 snapshot, so the pipeline's clock is pinned after that snapshot takes effect.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Span
from app.db.rls import bypass_rls
from app.ingest import pipeline
from app.pricing.prices import EFFECTIVE_FROM, PRICING_VERSION, SNAPSHOT_2026_10_01
from tests.helpers import Workspace, bearer, new_span_id, new_trace_id, span

HAIKU_45 = "claude-haiku-4-5"
# 19 uncached + 6,404 written with the 1-hour TTL; 12 output tokens.
ONE_HOUR_WRITE_USAGE = {
    "input_tokens": 6423,
    "output_tokens": 12,
    "cached_tokens": 0,
    "cache_write_tokens": 6404,
    "cache_write_1h_tokens": 6404,
}
ONE_HOUR_WRITE_COST = "0.01288700"

PINNED_NOW = EFFECTIVE_FROM + timedelta(hours=1)


@pytest.fixture
def clock_after_snapshot(monkeypatch: pytest.MonkeyPatch) -> datetime:
    """Pin the ingestion clock an hour after the 2026-10-15 prices take effect."""

    class _Clock(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return PINNED_NOW.astimezone(tz) if tz is not None else PINNED_NOW.replace(tzinfo=None)

    monkeypatch.setattr(pipeline, "datetime", _Clock)
    return PINNED_NOW


async def _ingest(
    client: httpx.AsyncClient, workspace: Workspace, spans: list[Any]
) -> httpx.Response:
    return await client.post("/v1/traces", json={"spans": spans}, headers=bearer(workspace.api_key))


async def _stored_spans(session_factory: async_sessionmaker[AsyncSession]) -> list[Span]:
    async with session_factory() as db:
        await bypass_rls(db)
        return list((await db.scalars(select(Span).order_by(Span.span_id))).all())


def _haiku_span(trace_id: str, start: datetime, **overrides: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "name": "messages claude-haiku-4-5",
        "provider": "anthropic",
        "model": HAIKU_45,
        "usage": dict(ONE_HOUR_WRITE_USAGE),
    }
    fields.update(overrides)
    return span(trace_id=trace_id, start=start, **fields)


async def test_native_span_with_writes_is_priced(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
    clock_after_snapshot: datetime,
) -> None:
    start = clock_after_snapshot - timedelta(minutes=5)
    response = await _ingest(client, workspace, [_haiku_span(new_trace_id(), start)])

    assert response.status_code == 200, response.text
    assert response.json() == {"accepted": 1, "rejected": []}
    (stored,) = await _stored_spans(session_factory)
    assert stored.input_tokens == 6423
    assert stored.cached_tokens == 0
    assert stored.cache_write_tokens == 6404
    assert stored.cache_write_1h_tokens == 6404
    assert str(stored.cost_usd) == ONE_HOUR_WRITE_COST
    assert stored.pricing_version == PRICING_VERSION


async def test_reads_plus_writes_over_input_is_rejected(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    trace_id = new_trace_id()
    start = datetime.now(UTC) - timedelta(minutes=5)
    valid = _haiku_span(trace_id, start)
    invalid = _haiku_span(
        trace_id,
        start,
        usage={
            "input_tokens": 100,
            "output_tokens": 1,
            "cached_tokens": 60,
            "cache_write_tokens": 41,
        },
    )
    response = await _ingest(client, workspace, [invalid, valid])

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["accepted"] == 1
    assert body["rejected"] == [
        {
            "index": 0,
            "span_id": invalid["span_id"],
            "reason": (
                "usage.cache_write_tokens: cached_tokens + cache_write_tokens cannot exceed "
                "usage.input_tokens"
            ),
        }
    ]
    (stored,) = await _stored_spans(session_factory)
    assert stored.span_id == valid["span_id"]


async def test_1h_over_total_is_rejected(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    trace_id = new_trace_id()
    start = datetime.now(UTC) - timedelta(minutes=5)
    valid = _haiku_span(trace_id, start)
    invalid = _haiku_span(
        trace_id,
        start,
        usage={
            "input_tokens": 100,
            "output_tokens": 1,
            "cache_write_tokens": 10,
            "cache_write_1h_tokens": 11,
        },
    )
    response = await _ingest(client, workspace, [valid, invalid])

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["accepted"] == 1
    assert body["rejected"] == [
        {
            "index": 1,
            "span_id": invalid["span_id"],
            "reason": "usage.cache_write_1h_tokens: cannot exceed usage.cache_write_tokens",
        }
    ]
    (stored,) = await _stored_spans(session_factory)
    assert stored.span_id == valid["span_id"]


async def test_1h_without_total_is_rejected(
    client: httpx.AsyncClient, workspace: Workspace
) -> None:
    """A 1-hour count with no total: the database check passes NULL comparisons, so normalization
    refuses it instead of storing a subset of nothing."""
    invalid = _haiku_span(
        new_trace_id(),
        datetime.now(UTC) - timedelta(minutes=5),
        usage={"input_tokens": 100, "output_tokens": 1, "cache_write_1h_tokens": 5},
    )
    response = await _ingest(client, workspace, [invalid])

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["accepted"] == 0
    assert len(body["rejected"]) == 1
    assert body["rejected"][0]["reason"].startswith("usage.cache_write_1h_tokens:")


async def test_replay_without_write_fields_keeps_cost(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A span stored before the migration carries no write fields; replaying it changes nothing."""
    started = EFFECTIVE_FROM - timedelta(days=4)  # priced by the earlier snapshot, as it was
    batch = [
        _haiku_span(
            new_trace_id(),
            started,
            usage={"input_tokens": 6423, "output_tokens": 12, "cached_tokens": 0},
        )
    ]
    first = await _ingest(client, workspace, batch)
    assert first.json() == {"accepted": 1, "rejected": []}
    (before,) = await _stored_spans(session_factory)
    assert before.pricing_version == SNAPSHOT_2026_10_01.version
    assert before.cost_usd is not None
    assert (before.cache_write_tokens, before.cache_write_1h_tokens) == (None, None)

    second = await _ingest(client, workspace, batch)
    assert second.json() == {"accepted": 1, "rejected": []}
    (after,) = await _stored_spans(session_factory)
    assert after.cost_usd == before.cost_usd
    assert after.pricing_version == before.pricing_version
    assert (after.cache_write_tokens, after.cache_write_1h_tokens) == (None, None)


async def test_trace_detail_exposes_write_fields(
    client: httpx.AsyncClient, workspace: Workspace
) -> None:
    trace_id = new_trace_id()
    start = datetime.now(UTC) - timedelta(minutes=5)
    written = _haiku_span(trace_id, start, span_id=new_span_id())
    plain = span(trace_id=trace_id, start=start + timedelta(seconds=1))
    response = await _ingest(client, workspace, [written, plain])
    assert response.json() == {"accepted": 2, "rejected": []}

    shown = await workspace.owner.get(f"/api/v1/projects/{workspace.project_id}/traces/{trace_id}")

    assert shown.status_code == 200, shown.text
    by_id = {item["span_id"]: item for item in shown.json()["spans"]}
    assert by_id[written["span_id"]]["cache_write_tokens"] == 6404
    assert by_id[written["span_id"]]["cache_write_1h_tokens"] == 6404
    assert by_id[plain["span_id"]]["cache_write_tokens"] is None
    assert by_id[plain["span_id"]]["cache_write_1h_tokens"] is None
