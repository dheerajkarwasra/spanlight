"""What a gateway span says about the bill: a cut stream's reason, the tier, the search fee.

A stream that ends before the provider's final usage keeps the input count it already had,
stores the output count as unknown (never the placeholder of 1) and so no cost, and says why in
`usage.unreported_reason`. A complete answer carries the tier the provider billed at and the web
searches it ran, which price the span. A request option whose price Spanlight does not model is
named in `usage.pricing_modifier_unknown` and the span is priced at the standard rate.

The gateway runs on a clock an hour after the 2026-10-16 snapshot takes effect, so the priced
cases use that snapshot whatever the wall clock says.
"""

import json
import uuid
from collections.abc import AsyncIterator
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.gateway.execute import execute
from app.gateway.tracing import pricing_modifier_unknown
from app.pricing.prices import EFFECTIVE_FROM
from tests.budget_fixtures import app_on_clock
from tests.gateway.support import (
    CHAT_BODY,
    CHAT_COST,
    anthropic_frame,
    chat_frames,
    messages_request,
    read_chunks,
    read_to_end,
)
from tests.gateway_fixtures import GatewayContextFactory, GatewayWorkspace
from tests.gateway_support import (
    Fail,
    FakeProvider,
    ManualClock,
    fixture_text,
    read_gateway_span,
)

REASON = "usage.unreported_reason"
MODIFIER = "usage.pricing_modifier_unknown"
# `message_start` of a Messages stream: 100 input tokens and the placeholder of 1 output token.
START_MESSAGE: dict[str, Any] = {
    "id": "msg_test_cutspan01",
    "type": "message",
    "role": "assistant",
    "model": "claude-haiku-4-5-20251001",
    "content": [],
    "stop_reason": None,
    "usage": {
        "input_tokens": 100,
        "output_tokens": 1,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
    },
}
STREAM_HEAD = [
    anthropic_frame("message_start", {"message": START_MESSAGE}),
    anthropic_frame("content_block_start", {"index": 0, "content_block": {"type": "text"}}),
    anthropic_frame(
        "content_block_delta", {"index": 0, "delta": {"type": "text_delta", "text": "Hel"}}
    ),
]
STREAM_TAIL = [
    anthropic_frame("content_block_stop", {"index": 0}),
    anthropic_frame(
        "message_delta",
        {"delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 12}},
    ),
    anthropic_frame("message_stop", {}),
]
MESSAGES_BODY: dict[str, Any] = {
    "model": "claude-haiku-4-5",
    "max_tokens": 64,
    "messages": [{"role": "user", "content": "Hello"}],
}
# 1,000 input and 200 output tokens of Claude Haiku 4.5: 1,000 * 1.00 + 200 * 5.00 per million.
HAIKU_USAGE: dict[str, Any] = {
    "input_tokens": 1000,
    "output_tokens": 200,
    "cache_creation_input_tokens": 0,
    "cache_read_input_tokens": 0,
}
HAIKU_STANDARD_COST = Decimal("0.00200000")
HAIKU_MESSAGE: dict[str, Any] = {
    "id": "msg_test_tierfee01",
    "type": "message",
    "role": "assistant",
    "model": "claude-haiku-4-5-20251001",
    "content": [{"type": "text", "text": "Hello!"}],
    "stop_reason": "end_turn",
    "stop_sequence": None,
    "usage": HAIKU_USAGE,
}
TRUNCATE_AFTER_CHUNKS = 3


@pytest.fixture
async def gateway_app(
    gateway_settings: Settings, fake_provider: FakeProvider
) -> AsyncIterator[Any]:
    """The shared gateway app, an hour after the 2026-10-16 prices take effect."""
    clock = ManualClock(start=EFFECTIVE_FROM + timedelta(hours=1))
    async with app_on_clock(gateway_settings, fake_provider, clock) as application:
        yield application


async def test_gateway_cut_stream_span_has_null_output_and_reason(
    gateway_context: GatewayContextFactory,
    gateway_workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Review Focus 2: a stream the client closed early is unknown, never cheap."""
    fake_provider.reply_stream([*STREAM_HEAD, *STREAM_TAIL])
    context = await gateway_context(credential="anthropic")

    result = await execute(messages_request(), context)
    assert result.stream is not None
    received = await read_chunks(result.stream, len(STREAM_HEAD))
    await result.stream.aclose()

    assert received == STREAM_HEAD
    span = await read_gateway_span(session_factory, gateway_workspace.project_id)
    assert span["status"] == "error"
    assert span["status_message"].startswith("client disconnected after ")
    assert (span["input_tokens"], span["output_tokens"], span["cached_tokens"]) == (100, None, 0)
    assert span["cost_usd"] is None
    assert span["attributes"][REASON] == "client_disconnected"


async def test_stream_without_final_usage_is_no_final_usage(
    gateway_context: GatewayContextFactory,
    gateway_workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    fake_provider.reply_stream(STREAM_HEAD[:1])  # ends cleanly after `message_start`
    context = await gateway_context(credential="anthropic")

    result = await execute(messages_request(), context)
    assert result.stream is not None
    await read_to_end(result.stream)

    span = await read_gateway_span(session_factory, gateway_workspace.project_id)
    assert (span["input_tokens"], span["output_tokens"], span["cost_usd"]) == (100, None, None)
    assert span["attributes"][REASON] == "no_final_usage"


async def test_upstream_cut_mid_stream_is_stream_error(
    gateway_context: GatewayContextFactory,
    gateway_workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    fake_provider.reply_stream([*STREAM_HEAD, Fail()])
    context = await gateway_context(credential="anthropic")

    result = await execute(messages_request(), context)
    assert result.stream is not None
    await read_to_end(result.stream)

    span = await read_gateway_span(session_factory, gateway_workspace.project_id)
    assert "stream_error: the provider connection failed" in span["status_message"]
    assert (span["input_tokens"], span["output_tokens"], span["cost_usd"]) == (100, None, None)
    assert span["attributes"][REASON] == "stream_error"


async def _fault_every_call(
    workspace: GatewayWorkspace, scenario: str, params: dict[str, Any] | None = None
) -> None:
    """A fault profile for `scenario` that always fires, attached to the workspace's key."""
    project = f"/api/v1/projects/{workspace.project_id}/gateway"
    created = await workspace.owner.post(
        f"{project}/fault-profiles",
        json={"name": scenario, "scenario": scenario, "params": params or {}},
    )
    assert created.status_code == 201, created.text
    attached = await workspace.owner.patch(
        f"{project}/keys/{workspace.gateway_key_id}",
        json={"fault_profile_id": created.json()["id"]},
    )
    assert attached.status_code == 200, attached.text


async def test_lab_truncated_stream_is_stream_cut_by_fault(
    gateway_workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _fault_every_call(
        gateway_workspace, "truncated_stream", {"after_chunks": TRUNCATE_AFTER_CHUNKS}
    )
    fake_provider.reply_stream(chat_frames())

    async with gateway_workspace.gw.stream(
        "POST", "/gw/v1/chat/completions", json={**CHAT_BODY, "stream": True}
    ) as response:
        _ = [chunk async for chunk in response.aiter_raw()]

    assert response.status_code == 200
    span = await read_gateway_span(session_factory, gateway_workspace.project_id)
    assert span["status_message"].startswith("200 FAULT_TRUNCATED_STREAM: ")
    assert (span["output_tokens"], span["cost_usd"]) == (None, None)
    assert span["attributes"][REASON] == "stream_cut_by_fault"


async def test_lab_malformed_json_is_no_final_usage(
    gateway_workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The Lab cut a complete answer's body, so its usage was never read: unknown, not free."""
    await _fault_every_call(gateway_workspace, "malformed_json")
    fake_provider.reply_json(HAIKU_MESSAGE)

    response = await gateway_workspace.gw.post("/gw/v1/messages", json=MESSAGES_BODY)

    assert response.status_code == 200
    span = await read_gateway_span(session_factory, gateway_workspace.project_id)
    assert span["status_message"].startswith("200 FAULT_MALFORMED_JSON: ")
    assert (span["output_tokens"], span["cost_usd"]) == (None, None)
    assert span["attributes"][REASON] == "no_final_usage"


async def test_complete_stream_has_no_reason(
    gateway_context: GatewayContextFactory,
    gateway_workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    fake_provider.reply_stream([*STREAM_HEAD, *STREAM_TAIL])
    context = await gateway_context(credential="anthropic")

    result = await execute(messages_request(), context)
    assert result.stream is not None
    await read_to_end(result.stream)

    span = await read_gateway_span(session_factory, gateway_workspace.project_id)
    assert (span["status"], span["input_tokens"], span["output_tokens"]) == ("ok", 100, 12)
    assert span["cost_usd"] is not None
    assert REASON not in span["attributes"]


async def test_non_stream_answer_without_output_count_is_no_final_usage(
    gateway_workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    no_output = {k: v for k, v in HAIKU_USAGE.items() if k != "output_tokens"}
    fake_provider.reply_json({**HAIKU_MESSAGE, "usage": no_output})

    response = await gateway_workspace.gw.post("/gw/v1/messages", json=MESSAGES_BODY)

    assert response.status_code == 200, response.text
    span = await read_gateway_span(session_factory, gateway_workspace.project_id)
    assert (span["input_tokens"], span["output_tokens"], span["cost_usd"]) == (1000, None, None)
    assert span["attributes"][REASON] == "no_final_usage"


async def test_non_stream_complete_answer_has_no_reason(
    gateway_workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    fake_provider.reply_json(HAIKU_MESSAGE)

    response = await gateway_workspace.gw.post("/gw/v1/messages", json=MESSAGES_BODY)

    assert response.status_code == 200, response.text
    span = await read_gateway_span(session_factory, gateway_workspace.project_id)
    assert span["cost_usd"] == HAIKU_STANDARD_COST
    assert REASON not in span["attributes"]


async def test_non_stream_provider_error_has_no_reason(
    gateway_workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """An error answer reports no usage because nothing was generated, not because it was lost."""
    error = {"type": "error", "error": {"type": "invalid_request_error", "message": "bad"}}
    fake_provider.reply_json(error, status=400)

    response = await gateway_workspace.gw.post("/gw/v1/messages", json=MESSAGES_BODY)

    assert response.status_code == 400, response.text
    span = await read_gateway_span(session_factory, gateway_workspace.project_id)
    assert (span["status"], span["output_tokens"]) == ("error", None)
    assert REASON not in span["attributes"]


async def test_non_stream_anthropic_batch_tier_and_search_priced(
    gateway_workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    usage = {**HAIKU_USAGE, "service_tier": "batch", "server_tool_use": {"web_search_requests": 2}}
    fake_provider.reply_json({**HAIKU_MESSAGE, "usage": usage})

    response = await gateway_workspace.gw.post("/gw/v1/messages", json=MESSAGES_BODY)

    assert response.status_code == 200, response.text
    span = await read_gateway_span(session_factory, gateway_workspace.project_id)
    assert (span["service_tier"], span["web_search_requests"]) == ("batch", 2)
    assert span["fee_usd"] == Decimal("0.02000000")  # 2 searches at $10.00 per 1,000
    assert span["cost_usd"] == HAIKU_STANDARD_COST / 2 + Decimal("0.02")
    assert REASON not in span["attributes"]


async def test_openai_chat_top_level_service_tier_flex(
    gateway_workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    body = {**_fixture_json("openai/chat.json"), "service_tier": "flex"}
    fake_provider.reply_json(body)

    response = await gateway_workspace.gw.post("/gw/v1/chat/completions", json=CHAT_BODY)

    assert response.status_code == 200, response.text
    span = await read_gateway_span(session_factory, gateway_workspace.project_id)
    assert (span["service_tier"], span["fee_usd"]) == ("flex", None)
    assert span["cost_usd"] == CHAT_COST / 2


async def test_unknown_tier_spelling_is_kept_raw(
    gateway_workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    body = {**_fixture_json("openai/chat.json"), "service_tier": "Turbo"}
    fake_provider.reply_json(body)

    response = await gateway_workspace.gw.post("/gw/v1/chat/completions", json=CHAT_BODY)

    assert response.status_code == 200, response.text
    span = await read_gateway_span(session_factory, gateway_workspace.project_id)
    assert span["service_tier"] is None
    assert span["attributes"]["usage.service_tier_raw"] == "Turbo"
    assert span["cost_usd"] == CHAT_COST  # an unknown tier is priced at the standard rate


async def test_responses_web_search_calls_counted(
    gateway_workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    body = _fixture_json("openai/responses.json")
    searches = [_search_call("completed"), _search_call("completed"), _search_call("failed")]
    fake_provider.reply_json({**body, "output": [*searches, *body["output"]]})

    response = await gateway_workspace.gw.post(
        "/gw/v1/responses", json={"model": "gpt-4.1-mini", "input": "Hello"}
    )

    assert response.status_code == 200, response.text
    span = await read_gateway_span(session_factory, gateway_workspace.project_id)
    assert (span["service_tier"], span["web_search_requests"]) == ("standard", 2)
    assert span["fee_usd"] == Decimal("0.02000000")
    assert span["cost_usd"] == CHAT_COST + Decimal("0.02")  # the same usage as the chat fixture


@pytest.mark.parametrize(
    ("extra", "expected"),
    [
        ({"inference_geo": "us"}, "inference_geo_us"),
        ({"speed": "fast"}, "fast_mode"),
        ({}, None),
    ],
)
async def test_request_with_inference_geo_or_fast_speed_marks_unmodelled_modifier(
    gateway_workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
    extra: dict[str, Any],
    expected: str | None,
) -> None:
    fake_provider.reply_json(HAIKU_MESSAGE)

    response = await gateway_workspace.gw.post("/gw/v1/messages", json={**MESSAGES_BODY, **extra})

    assert response.status_code == 200, response.text
    span = await read_gateway_span(session_factory, gateway_workspace.project_id)
    assert span["attributes"].get(MODIFIER, "absent") == (expected or "absent")
    assert span["cost_usd"] == HAIKU_STANDARD_COST  # the standard rate: the modifier is unmodelled


async def test_unbilled_provider_error_names_no_modifier(
    gateway_workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A call that reported no usage was not billed, so it must not count as unmodelled."""
    error = {"type": "error", "error": {"type": "invalid_request_error", "message": "bad"}}
    fake_provider.reply_json(error, status=400)

    response = await gateway_workspace.gw.post(
        "/gw/v1/messages", json={**MESSAGES_BODY, "inference_geo": "us"}
    )

    assert response.status_code == 400, response.text
    span = await read_gateway_span(session_factory, gateway_workspace.project_id)
    assert span["input_tokens"] is None
    assert MODIFIER not in span["attributes"]


async def test_fault_before_the_provider_names_no_modifier(
    gateway_workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _fault_every_call(gateway_workspace, "rate_limited")

    response = await gateway_workspace.gw.post(
        "/gw/v1/messages", json={**MESSAGES_BODY, "speed": "fast"}
    )

    assert response.status_code == 429, response.text
    assert fake_provider.requests == []
    span = await read_gateway_span(session_factory, gateway_workspace.project_id)
    assert span["input_tokens"] is None
    assert MODIFIER not in span["attributes"]


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"inference_geo": "us"}, "inference_geo_us"),
        ({"inference_geo": "  EU "}, "inference_geo_eu"),
        ({"inference_geo": "global"}, None),
        ({"inference_geo": "  "}, None),
        ({"inference_geo": 1}, None),
        ({"inference_geo": " GLOBAL "}, None),
        ({"inference_geo": "US-East_1"}, "inference_geo_us-east_1"),
        ({"inference_geo": "x" * 32}, f"inference_geo_{'x' * 32}"),
        ({"inference_geo": "x" * 33}, "inference_geo_other"),
        ({"inference_geo": "eu west"}, "inference_geo_other"),
        ({"inference_geo": "eu.west"}, "inference_geo_other"),
        ({"inference_geo": "日本"}, "inference_geo_other"),
        ({"speed": "fast"}, "fast_mode"),
        ({"speed": " FAST "}, "fast_mode"),
        ({"speed": "slow"}, None),
        ({"inference_geo": "us", "speed": "fast"}, "inference_geo_us"),
        ({"inference_geo": "global", "speed": "fast"}, "fast_mode"),
        ({}, None),
    ],
)
def test_pricing_modifier_unknown_matches_the_sdk_rule(
    body: dict[str, Any], expected: str | None
) -> None:
    assert pricing_modifier_unknown(body) == expected


def _fixture_json(relative: str) -> dict[str, Any]:
    parsed: dict[str, Any] = json.loads(fixture_text(relative))
    return parsed


def _search_call(status: str) -> dict[str, Any]:
    return {
        "id": f"ws_test_{uuid.uuid4().hex}",
        "type": "web_search_call",
        "status": status,
        "action": {"type": "search", "query": "spanlight"},
    }
