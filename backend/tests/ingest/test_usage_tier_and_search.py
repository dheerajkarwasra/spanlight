"""Service tier, web search counts and fees on the native API: stored, priced, read back.

A Claude Sonnet 4.6 call priced by the 2026-10-19 snapshot: $3 / $15 per million tokens, the
Anthropic batch multiplier 0.5 and the Anthropic web search fee of $10 per 1,000 requests. The
pipeline's clock is pinned after that snapshot takes effect, so every span here can start in it.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Trace
from app.db.rls import bypass_rls
from app.ingest import pipeline
from app.pricing.prices import EFFECTIVE_FROM, PRICING_VERSION, SNAPSHOT_2026_10_15
from tests.helpers import Workspace, new_span_id, new_trace_id, span
from tests.ingest.support import ingest_native, stored_spans

SONNET_46 = "claude-sonnet-4-6"
# 20,000 input tokens at $3 and 1,000 output tokens at $15 per million: $0.075 at the standard
# rate, $0.0375 at the batch rate.
USAGE = {"input_tokens": 20_000, "output_tokens": 1_000, "cached_tokens": 0}
STANDARD_COST = Decimal("0.07500000")
BATCH_TOKEN_COST = Decimal("0.03750000")
THREE_SEARCHES_FEE = Decimal("0.03000000")

SPAN_START = EFFECTIVE_FROM + timedelta(hours=12)  # 2026-10-19T12:00Z
PINNED_NOW = SPAN_START + timedelta(hours=1)


@pytest.fixture(autouse=True)
def clock_after_snapshot(monkeypatch: pytest.MonkeyPatch) -> datetime:
    """Pin the ingestion clock after the current prices and fees take effect."""

    class _Clock(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return PINNED_NOW.astimezone(tz) if tz is not None else PINNED_NOW.replace(tzinfo=None)

    monkeypatch.setattr(pipeline, "datetime", _Clock)
    return PINNED_NOW


def _sonnet_span(
    trace_id: str,
    *,
    start: datetime = SPAN_START,
    usage: dict[str, Any] | None = None,
    **fields: Any,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "messages claude-sonnet-4-6",
        "provider": "anthropic",
        "model": SONNET_46,
        "usage": usage if usage is not None else dict(USAGE),
    }
    body.update(fields)
    return span(trace_id=trace_id, start=start, **body)


def _batch_with_searches(trace_id: str, **fields: Any) -> dict[str, Any]:
    usage = {**USAGE, "service_tier": "batch", "server_tool_use": {"web_search_requests": 3}}
    return _sonnet_span(trace_id, usage=usage, **fields)


async def _stored_trace(session_factory: async_sessionmaker[AsyncSession], trace_id: str) -> Trace:
    async with session_factory() as db:
        await bypass_rls(db)
        return (await db.scalars(select(Trace).where(Trace.trace_id == trace_id))).one()


async def test_native_span_with_batch_tier_and_searches_is_priced(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    response = await ingest_native(client, workspace, [_batch_with_searches(new_trace_id())])

    assert response.status_code == 200, response.text
    assert response.json() == {"accepted": 1, "rejected": []}
    (stored,) = await stored_spans(session_factory)
    assert stored.service_tier == "batch"
    assert stored.web_search_requests == 3
    assert stored.fee_usd == THREE_SEARCHES_FEE
    assert stored.cost_usd == BATCH_TOKEN_COST + THREE_SEARCHES_FEE
    assert stored.pricing_version == PRICING_VERSION == "2026-10-19"


async def test_invalid_tier_rejects_that_span_only(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    trace_id = new_trace_id()
    invalid = _sonnet_span(trace_id, usage={**USAGE, "service_tier": "fast"})
    valid = _sonnet_span(trace_id, start=SPAN_START + timedelta(seconds=1))

    response = await ingest_native(client, workspace, [invalid, valid])

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["accepted"] == 1
    (rejected,) = body["rejected"]
    assert rejected["index"] == 0
    assert rejected["span_id"] == invalid["span_id"]
    assert rejected["reason"].startswith("usage.service_tier: ")
    (stored,) = await stored_spans(session_factory)
    assert stored.span_id == valid["span_id"]
    assert stored.service_tier is None


@pytest.mark.parametrize(
    ("server_tool_use", "location"),
    [
        ({"web_search_requests": 100_001}, "usage.server_tool_use.web_search_requests: "),
        ({"web_search_requests": -1}, "usage.server_tool_use.web_search_requests: "),
        ({"web_search_requests": "3"}, "usage.server_tool_use.web_search_requests: "),
        ({"web_search_requests": 3.0}, "usage.server_tool_use.web_search_requests: "),
    ],
)
async def test_web_search_over_limit_rejected(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
    server_tool_use: dict[str, Any],
    location: str,
) -> None:
    sent = _sonnet_span(new_trace_id(), usage={**USAGE, "server_tool_use": server_tool_use})

    response = await ingest_native(client, workspace, [sent])

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["accepted"] == 0
    (rejected,) = body["rejected"]
    assert rejected["reason"].startswith(location)
    assert await stored_spans(session_factory) == []


async def test_largest_search_count_and_other_server_tools_are_accepted(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The count's upper bound is inclusive; other `server_tool_use` keys are ignored."""
    usage = {
        **USAGE,
        "server_tool_use": {"web_search_requests": 100_000, "web_fetch_requests": 2},
    }
    response = await ingest_native(client, workspace, [_sonnet_span(new_trace_id(), usage=usage)])

    assert response.json() == {"accepted": 1, "rejected": []}
    (stored,) = await stored_spans(session_factory)
    assert stored.web_search_requests == 100_000
    assert stored.fee_usd == Decimal("1000.00000000")
    assert stored.cost_usd == STANDARD_COST + Decimal(1000)


async def test_output_null_with_reason_marks_trace_unpriced(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A stream cut before its final usage is unknown, not cheap (Review Focus 2)."""
    trace_id = new_trace_id()
    cut = _sonnet_span(
        trace_id,
        usage={"input_tokens": 20_000, "cached_tokens": 0},
        attributes={"usage.unreported_reason": "client_disconnected"},
    )

    response = await ingest_native(client, workspace, [cut])

    assert response.json() == {"accepted": 1, "rejected": []}
    (stored,) = await stored_spans(session_factory)
    assert stored.input_tokens == 20_000
    assert stored.output_tokens is None
    assert stored.cost_usd is None
    assert stored.pricing_version is None
    assert stored.attributes["usage.unreported_reason"] == "client_disconnected"
    trace = await _stored_trace(session_factory, trace_id)
    assert trace.has_unpriced is True
    assert trace.cost_usd is None
    assert trace.output_tokens == 0


async def test_cut_stream_with_searches_keeps_fee_without_cost(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A fee is known without the token cost: stored, while the cost stays unknown."""
    trace_id = new_trace_id()
    cut = _sonnet_span(
        trace_id,
        usage={"input_tokens": 20_000, "server_tool_use": {"web_search_requests": 3}},
        attributes={"usage.unreported_reason": "stream_error"},
    )

    response = await ingest_native(client, workspace, [cut])

    assert response.json() == {"accepted": 1, "rejected": []}
    (stored,) = await stored_spans(session_factory)
    assert stored.fee_usd == THREE_SEARCHES_FEE
    assert stored.cost_usd is None
    assert stored.pricing_version is None
    trace = await _stored_trace(session_factory, trace_id)
    assert trace.has_unpriced is True
    assert trace.cost_usd is None


async def test_replay_without_tier_or_search_keeps_cost(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A span sent before the new fields existed replays to the same cost (Review Focus 5)."""
    started = SNAPSHOT_2026_10_15.effective_from + timedelta(hours=12)
    batch = [_sonnet_span(new_trace_id(), start=started)]

    first = await ingest_native(client, workspace, batch)
    assert first.json() == {"accepted": 1, "rejected": []}
    (before,) = await stored_spans(session_factory)
    assert before.cost_usd == STANDARD_COST
    assert before.pricing_version == SNAPSHOT_2026_10_15.version
    assert (before.service_tier, before.web_search_requests, before.fee_usd) == (None, None, None)

    second = await ingest_native(client, workspace, batch)
    assert second.json() == {"accepted": 1, "rejected": []}
    (after,) = await stored_spans(session_factory)
    assert after.cost_usd == before.cost_usd
    assert after.pricing_version == before.pricing_version
    assert (after.service_tier, after.web_search_requests, after.fee_usd) == (None, None, None)


async def test_replay_with_fee_is_idempotent(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    trace_id = new_trace_id()
    batch = [_batch_with_searches(trace_id)]

    first = await ingest_native(client, workspace, batch)
    assert first.json() == {"accepted": 1, "rejected": []}
    (before,) = await stored_spans(session_factory)
    trace_before = await _stored_trace(session_factory, trace_id)

    second = await ingest_native(client, workspace, batch)
    assert second.json() == {"accepted": 1, "rejected": []}
    (after,) = await stored_spans(session_factory)
    trace_after = await _stored_trace(session_factory, trace_id)
    assert (after.cost_usd, after.fee_usd) == (before.cost_usd, before.fee_usd)
    assert after.cost_usd == BATCH_TOKEN_COST + THREE_SEARCHES_FEE
    assert trace_after.cost_usd == trace_before.cost_usd == after.cost_usd


async def test_blank_provider_is_stored_as_none_and_charges_no_fee(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Whitespace around a provider is dropped; a blank one is no provider, so no fee."""
    trace_id = new_trace_id()
    blank = _batch_with_searches(trace_id, provider="   ")
    padded = _batch_with_searches(
        trace_id, provider=" anthropic ", start=SPAN_START + timedelta(seconds=1)
    )

    response = await ingest_native(client, workspace, [blank, padded])

    assert response.json() == {"accepted": 2, "rejected": []}
    by_id = {stored.span_id: stored for stored in await stored_spans(session_factory)}
    assert by_id[blank["span_id"]].provider is None
    assert by_id[blank["span_id"]].fee_usd is None
    assert by_id[padded["span_id"]].provider == "anthropic"
    assert by_id[padded["span_id"]].fee_usd == THREE_SEARCHES_FEE


async def test_trace_detail_exposes_tier_search_and_fee(
    client: httpx.AsyncClient, workspace: Workspace
) -> None:
    trace_id = new_trace_id()
    searched = _batch_with_searches(trace_id, span_id=new_span_id())
    plain = _sonnet_span(trace_id, start=SPAN_START + timedelta(seconds=1))
    response = await ingest_native(client, workspace, [searched, plain])
    assert response.json() == {"accepted": 2, "rejected": []}

    shown = await workspace.owner.get(f"/api/v1/projects/{workspace.project_id}/traces/{trace_id}")

    assert shown.status_code == 200, shown.text
    by_id = {item["span_id"]: item for item in shown.json()["spans"]}
    assert by_id[searched["span_id"]]["service_tier"] == "batch"
    assert by_id[searched["span_id"]]["web_search_requests"] == 3
    assert by_id[searched["span_id"]]["fee_usd"] == "0.03000000"
    assert by_id[searched["span_id"]]["cost_usd"] == "0.06750000"
    assert by_id[plain["span_id"]]["service_tier"] is None
    assert by_id[plain["span_id"]]["web_search_requests"] is None
    assert by_id[plain["span_id"]]["fee_usd"] is None
