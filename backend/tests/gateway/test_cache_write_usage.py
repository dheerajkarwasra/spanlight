"""Anthropic cache writes through the gateway: kept, counted once on a stream, priced, cached.

The bodies are shaped like the 2026-10-14 pilot's real Claude Haiku 4.5 call
(`docs/plans/reports/pilot-2026-10-14.md`, D1): 19 uncached input tokens, a 6,404-token cache
write with the 1-hour TTL, 12 output tokens; Anthropic billed $0.012887. The priced cases run
the gateway on a clock an hour after the 2026-10-15 snapshot takes effect, so they are priced
by that snapshot whatever the wall clock says.
"""

import logging
from collections.abc import AsyncIterator
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.gateway.cache import usage_from_json
from app.gateway.sse import SseFrame, Usage
from app.gateway.stream_observers import AnthropicMessagesObserver
from app.gateway.usage import anthropic_usage
from app.pricing.prices import EFFECTIVE_FROM
from tests.budget_fixtures import app_on_clock
from tests.gateway.support import anthropic_frame
from tests.gateway_fixtures import GatewayWorkspace
from tests.gateway_support import FakeProvider, ManualClock, logged, read_gateway_spans

# The pilot's usage, as Anthropic sent it.
PILOT_USAGE: dict[str, Any] = {
    "input_tokens": 19,
    "output_tokens": 12,
    "cache_read_input_tokens": 0,
    "cache_creation_input_tokens": 6404,
    "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 6404},
}
# The same usage in the native convention: `input_tokens` is the whole prompt.
PILOT_NATIVE = Usage(
    input_tokens=6423,
    output_tokens=12,
    cached_tokens=0,
    cache_write_tokens=6404,
    cache_write_1h_tokens=6404,
)
# 19 uncached * 1.00 + 6404 written (1 h) * 2.00 + 12 output * 5.00, per million tokens.
PILOT_COST = Decimal("0.01288700")
PILOT_MESSAGE: dict[str, Any] = {
    "id": "msg_test_cachewrite01",
    "type": "message",
    "role": "assistant",
    "model": "claude-haiku-4-5-20251001",
    "content": [{"type": "text", "text": "Hello!"}],
    "stop_reason": "end_turn",
    "stop_sequence": None,
    "usage": PILOT_USAGE,
}
MESSAGES_BODY: dict[str, Any] = {
    "model": "claude-haiku-4-5",
    "max_tokens": 64,
    "messages": [{"role": "user", "content": "Hello"}],
}


@pytest.fixture
def pricing_clock() -> ManualClock:
    """An hour after the 2026-10-15 prices take effect."""
    return ManualClock(start=EFFECTIVE_FROM + timedelta(hours=1))


@pytest.fixture
async def gateway_app(
    gateway_settings: Settings, fake_provider: FakeProvider, pricing_clock: ManualClock
) -> AsyncIterator[Any]:
    """The shared gateway app, on `pricing_clock` instead of real time."""
    async with app_on_clock(gateway_settings, fake_provider, pricing_clock) as application:
        yield application


def _frame(event: str, data: dict[str, Any]) -> SseFrame:
    raw = anthropic_frame(event, data).decode()
    _, _, payload = raw.partition("data: ")
    return SseFrame(event=event, data=payload.strip())


def test_anthropic_usage_keeps_write_split() -> None:
    assert anthropic_usage(PILOT_USAGE) == PILOT_NATIVE


def test_anthropic_usage_without_split_leaves_the_1h_part_unknown() -> None:
    usage = {k: v for k, v in PILOT_USAGE.items() if k != "cache_creation"}
    assert anthropic_usage(usage) == Usage(6423, 12, 0, 6404, None)


@pytest.mark.parametrize(
    ("write_total", "split_1h"),
    [(None, 6404), (6000, 6404)],
    ids=["no-total", "larger-than-total"],
)
def test_anthropic_usage_drops_a_1h_part_that_does_not_fit(
    write_total: int | None, split_1h: int, caplog: pytest.LogCaptureFixture
) -> None:
    """Ingestion would refuse the whole span for such a part; the call's record comes first.

    The drop is logged with both counts (never the payload), so a provider change is visible.
    """
    usage = {**PILOT_USAGE, "cache_creation": {"ephemeral_1h_input_tokens": split_1h}}
    if write_total is None:
        del usage["cache_creation_input_tokens"]
    else:
        usage["cache_creation_input_tokens"] = write_total

    with caplog.at_level(logging.WARNING, logger="app.gateway.usage"):
        result = anthropic_usage(usage)

    assert result is not None
    assert (result.cache_write_tokens, result.cache_write_1h_tokens) == (write_total, None)
    [dropped] = logged(caplog, "gateway.usage_1h_split_dropped")
    assert (dropped["cache_write_tokens"], dropped["cache_write_1h_tokens"]) == (
        write_total,
        split_1h,
    )


def test_anthropic_usage_consistent_split_logs_nothing(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="app.gateway.usage"):
        assert anthropic_usage(PILOT_USAGE) == PILOT_NATIVE
    assert logged(caplog, "gateway.usage_1h_split_dropped") == []


def test_stream_cache_write_counted_once() -> None:
    """`message_start` carries the split; `message_delta` repeats the cumulative counts."""
    observer = AnthropicMessagesObserver()
    start_usage = {**PILOT_USAGE, "output_tokens": 1}
    delta_usage = {k: v for k, v in PILOT_USAGE.items() if k != "cache_creation"}
    observer.on_frame(_frame("message_start", {"message": {**PILOT_MESSAGE, "usage": start_usage}}))
    observer.on_frame(
        _frame("message_delta", {"delta": {"stop_reason": "end_turn"}, "usage": delta_usage})
    )

    assert observer.summary().usage == PILOT_NATIVE


def test_stored_usage_without_write_fields_reads_as_unknown() -> None:
    """An entry stored before the write fields existed has no such keys: unknown, not 0."""
    stored = {"input_tokens": 6423, "output_tokens": 12, "cached_tokens": 0}
    assert usage_from_json(stored) == Usage(6423, 12, 0, None, None)


async def test_gateway_span_priced_with_writes(
    gateway_workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    fake_provider.reply_json(PILOT_MESSAGE)

    response = await gateway_workspace.gw.post("/gw/v1/messages", json=MESSAGES_BODY)

    assert response.status_code == 200, response.text
    [span] = await read_gateway_spans(session_factory, gateway_workspace.project_id)
    assert (span["status"], span["provider"]) == ("ok", "anthropic")
    assert (span["input_tokens"], span["output_tokens"], span["cached_tokens"]) == (6423, 12, 0)
    assert (span["cache_write_tokens"], span["cache_write_1h_tokens"]) == (6404, 6404)
    assert span["cost_usd"] == PILOT_COST


async def test_cache_hit_keeps_write_fields_in_original_usage(
    gateway_workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    keys = f"/api/v1/projects/{gateway_workspace.project_id}/gateway/keys"
    edited = await gateway_workspace.owner.patch(
        f"{keys}/{gateway_workspace.gateway_key_id}", json={"cache_ttl_seconds": 600}
    )
    assert edited.status_code == 200, edited.text
    fake_provider.reply_json(PILOT_MESSAGE)  # one upstream answer: the second call is a hit

    first = await gateway_workspace.gw.post("/gw/v1/messages", json=MESSAGES_BODY)
    second = await gateway_workspace.gw.post("/gw/v1/messages", json=MESSAGES_BODY)

    assert (first.status_code, first.headers["x-spanlight-cache"]) == (200, "miss")
    assert (second.status_code, second.headers["x-spanlight-cache"]) == (200, "hit")
    assert second.json() == PILOT_MESSAGE
    assert len(fake_provider.requests) == 1
    spans = await read_gateway_spans(session_factory, gateway_workspace.project_id)
    # Both spans start at the same pinned instant, so they are told apart by their cache status.
    by_cache = {span["attributes"]["spanlight.cache"]: span for span in spans}
    assert sorted(by_cache) == ["hit", "miss"]
    billed, hit = by_cache["miss"], by_cache["hit"]
    assert (billed["cache_write_tokens"], billed["cost_usd"]) == (6404, PILOT_COST)
    assert (hit["input_tokens"], hit["output_tokens"], hit["cost_usd"]) == (0, 0, Decimal("0"))
    assert (hit["cache_write_tokens"], hit["cache_write_1h_tokens"]) == (0, 0)
    assert hit["attributes"]["spanlight.cache.original_usage"] == {
        "input_tokens": 6423,
        "output_tokens": 12,
        "cached_tokens": 0,
        "cache_write_tokens": 6404,
        "cache_write_1h_tokens": 6404,
    }
