"""A caller that goes away mid-call: the provider response is closed and the call is one span.

The client may close its stream after reading part of it, close it without reading it, or cancel
the call before any answer exists. Each way the span says the client disconnected, carries no
usage (the provider never reported any) and so no cost, never a zero one.
"""

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.observability import GATEWAY_ATTEMPTS, GATEWAY_REQUESTS
from app.gateway.execute import execute
from tests.gateway.interruptions import close_unread, disconnect_mid_stream
from tests.gateway.support import chat_frames, chat_request, key_minute_counts
from tests.gateway_fixtures import GatewayContextFactory, GatewayWorkspace
from tests.gateway_support import FakeProvider, Stall, metric_delta, read_gateway_span


@dataclass(frozen=True)
class _Thinking(Stall):
    """A provider that never answers; `arrived` is set once the request has reached it."""

    arrived: asyncio.Event = field(default_factory=asyncio.Event)

    async def wait(self) -> None:
        self.arrived.set()
        await super().wait()


def _assert_disconnect_span(span: dict[str, Any]) -> None:
    assert (span["kind"], span["status"]) == ("llm", "error")
    assert span["status_message"].startswith("client disconnected after ")
    assert span["attributes"]["spanlight.gateway.client_disconnected"] is True
    assert (span["input_tokens"], span["output_tokens"], span["cached_tokens"]) == (
        None,
        None,
        None,
    )
    assert span["cost_usd"] is None  # no usage reported: unknown, never 0


async def test_client_disconnect_mid_stream_records_one_error_span(
    gateway_context: GatewayContextFactory,
    gateway_workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    served = metric_delta(GATEWAY_REQUESTS, surface="chat_completions", outcome="ok")
    context = await gateway_context(keyed=True)

    call = await disconnect_mid_stream(fake_provider, context)

    assert call.received == call.scripted[:2]
    assert (fake_provider.streams_opened, fake_provider.streams_closed) == (1, 1)
    span = await read_gateway_span(session_factory, gateway_workspace.project_id)
    _assert_disconnect_span(span)
    assert span["span_id"] == call.result.span_id
    assert span["output"] == {"role": "assistant", "content": "Hello"}
    assert span["attributes"]["spanlight.gateway.attempts"] == 1
    # The key's minute counts the call, with no tokens: none were reported.
    assert await key_minute_counts(session_factory, gateway_workspace.gateway_key_id) == (1, 0)
    # Current behaviour: the outcome metric is counted when the first byte arrives
    # (`Call._finish`), so a stream the client then abandoned still counts as `ok`.
    assert served.value == 1


async def test_closing_a_stream_nobody_read_closes_upstream_and_records_a_disconnect(
    gateway_context: GatewayContextFactory,
    gateway_workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    context = await gateway_context(keyed=True)

    await close_unread(fake_provider, context)  # closes twice: harmless, records nothing more

    assert (fake_provider.streams_opened, fake_provider.streams_closed) == (1, 1)
    span = await read_gateway_span(session_factory, gateway_workspace.project_id)
    _assert_disconnect_span(span)
    assert span["output"] is None
    assert span["attributes"]["spanlight.gateway.attempts"] == 1
    assert await key_minute_counts(session_factory, gateway_workspace.gateway_key_id) == (1, 0)


async def test_cancelling_execute_before_an_answer_records_a_499_disconnect(
    gateway_context: GatewayContextFactory,
    gateway_workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    thinking = _Thinking()
    fake_provider.reply_stream(chat_frames(), before_headers=thinking)
    cancelled = metric_delta(GATEWAY_ATTEMPTS, provider="openai", status="cancelled")
    context = await gateway_context(keyed=True)

    call = asyncio.create_task(execute(chat_request(), context))
    await asyncio.wait_for(thinking.arrived.wait(), 5)  # the request has reached the provider
    call.cancel()
    with pytest.raises(asyncio.CancelledError):
        await call

    assert fake_provider.streams_opened == 0  # no response was ever handed out, none leaked
    assert cancelled.value == 1
    span = await read_gateway_span(session_factory, gateway_workspace.project_id)
    _assert_disconnect_span(span)
    assert span["output"] is None
    # The one request the provider received is on the span, with no status: it never answered.
    assert span["attributes"]["spanlight.gateway.attempts"] == 1
    assert span["attributes"]["spanlight.gateway.upstream_status"] is None
    assert span["provider"] == "openai"
    assert await key_minute_counts(session_factory, gateway_workspace.gateway_key_id) == (1, 0)
