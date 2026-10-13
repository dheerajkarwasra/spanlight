"""Retries and fallbacks before the first byte, and what the span says about them.

A stored route retries a timeout (`max_attempts=2` by default) and falls back on a 5xx; the
in-memory route of judges and explanations makes one attempt and never falls back. A retried
attempt may already have been billed by the provider, yet the span carries only the last
attempt's usage: `spanlight.gateway.attempts` is the only trace of the first.
"""

import dataclasses
import json
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.gateway.execute import execute
from app.gateway.route_config import RetryPolicy, Target
from app.gateway.settle_helpers import may_have_billed
from tests.gateway.support import CHAT_COST, chat_frames, chat_request, read_to_end, with_route
from tests.gateway_fixtures import GatewayContextFactory, GatewayWorkspace
from tests.gateway_support import Fail, FakeProvider, Stall, read_gateway_span

OVERLOADED = {
    "error": {
        "message": "The server is overloaded. Please retry.",
        "type": "server_error",
        "param": None,
        "code": None,
    }
}


def _read_timeout() -> Fail:
    """The provider took the request, sent its headers, then nothing until the read timed out."""
    return Fail(httpx.ReadTimeout("timed out"))


async def test_timeout_before_first_byte_is_retried_and_the_span_shows_the_last_attempt(
    gateway_context: GatewayContextFactory,
    gateway_workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    fake_provider.reply_stream([_read_timeout()])  # may have generated (and billed) already
    fake_provider.reply_stream(chat_frames())  # the second attempt answers, with its usage
    waits: list[float] = []

    async def no_wait(seconds: float) -> None:
        waits.append(seconds)

    context = dataclasses.replace(await gateway_context(), sleep=no_wait)
    assert context.route.retry.max_attempts == 2  # the stored route's default
    result = await execute(chat_request(), context)
    assert result.stream is not None
    await read_to_end(result.stream)

    assert result.status == 200
    assert waits == [0.2]  # the route's default backoff before the retry
    assert (fake_provider.pending, fake_provider.streams_opened, fake_provider.open_streams) == (
        0,
        2,
        0,
    )
    span = await read_gateway_span(session_factory, gateway_workspace.project_id)
    assert (span["status"], span["status_message"]) == ("ok", None)
    assert span["attributes"]["spanlight.gateway.attempts"] == 2
    assert span["attributes"]["spanlight.gateway.fallbacks"] == 0
    # Only the answer that reached the client is on the span; the first attempt's possible
    # charge is not (a provider-side double bill the gateway cannot see).
    assert (span["input_tokens"], span["output_tokens"], span["cached_tokens"]) == (19, 9, 8)
    assert span["cost_usd"] == CHAT_COST


async def test_single_attempt_route_does_not_retry_a_timeout(
    gateway_context: GatewayContextFactory,
    gateway_workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    fake_provider.reply_stream([_read_timeout()])
    context = await gateway_context(credential="openai")

    result = await execute(chat_request(), context)

    assert (result.status, result.stream) == (504, None)
    assert result.headers["X-Spanlight-Attempts"] == "1"
    assert may_have_billed(result)  # the request was sent and got no error status back
    assert (len(fake_provider.requests), fake_provider.streams_opened) == (1, 1)
    assert fake_provider.open_streams == 0
    span = await read_gateway_span(session_factory, gateway_workspace.project_id)
    assert span["status"] == "error"
    assert span["status_message"].startswith("504 UPSTREAM_TIMEOUT: ")
    assert span["attributes"]["spanlight.gateway.attempts"] == 1
    assert (span["input_tokens"], span["cost_usd"]) == (None, None)


async def test_a_provider_that_hangs_past_the_budget_is_not_retried(
    gateway_context: GatewayContextFactory,
    gateway_workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # One attempt may use the whole budget, so a hang leaves no time for the retry the stored
    # route would allow. Any second request would be unscripted and fail the test.
    fake_provider.reply_stream(chat_frames(), before_headers=Stall())
    context = with_route(await gateway_context(), timeout_ms=200)

    result = await execute(chat_request(), context)

    assert (result.status, result.stream) == (504, None)
    assert len(fake_provider.requests) == 1
    assert fake_provider.streams_opened == 0  # the hang was before the headers: nothing to close
    span = await read_gateway_span(session_factory, gateway_workspace.project_id)
    assert span["status_message"].startswith("504 UPSTREAM_TIMEOUT: ")
    assert span["attributes"]["spanlight.gateway.attempts"] == 1
    assert (span["input_tokens"], span["cost_usd"]) == (None, None)


async def test_upstream_5xx_passes_through_and_falls_back(
    gateway_context: GatewayContextFactory,
    gateway_workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    fake_provider.reply_json(OVERLOADED, status=503, headers={"retry-after": "0"})
    fake_provider.reply_stream(chat_frames())
    openai = Target(credential_id=gateway_workspace.openai_credential_id)
    # Two targets on the OpenAI credential, one attempt each: the 503 falls straight back.
    context = with_route(
        await gateway_context(), targets=[openai, openai], retry=RetryPolicy(max_attempts=1)
    )

    result = await execute(chat_request(), context)
    assert result.stream is not None
    await read_to_end(result.stream)

    assert result.status == 200
    assert (fake_provider.pending, fake_provider.open_streams) == (0, 0)
    span = await read_gateway_span(session_factory, gateway_workspace.project_id)
    attributes: dict[str, Any] = span["attributes"]
    assert (span["status"], span["status_message"]) == ("ok", None)
    assert (
        attributes["spanlight.gateway.attempts"],
        attributes["spanlight.gateway.fallbacks"],
    ) == (
        2,
        1,
    )
    assert attributes["spanlight.gateway.upstream_status"] == 200
    assert (span["input_tokens"], span["output_tokens"], span["cached_tokens"]) == (19, 9, 8)
    assert span["cost_usd"] == CHAT_COST


async def test_upstream_5xx_without_a_fallback_passes_through_byte_for_byte(
    gateway_context: GatewayContextFactory,
    gateway_workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    body = json.dumps(OVERLOADED, indent=2).encode()  # not the gateway's own JSON spacing
    fake_provider.reply_json(body, status=503, headers={"retry-after": "0"})
    context = await gateway_context(credential="openai")

    result = await execute(chat_request(), context)

    assert (result.status, result.stream, result.body) == (503, None, body)
    assert result.headers["X-Spanlight-Code"] == "UPSTREAM_ERROR"
    assert fake_provider.open_streams == 0
    span = await read_gateway_span(session_factory, gateway_workspace.project_id)
    assert span["status"] == "error"
    assert span["status_message"] == "503 server_error: The server is overloaded. Please retry."
    assert span["attributes"]["spanlight.gateway.attempts"] == 1
    assert span["attributes"]["spanlight.gateway.upstream_status"] == 503
    assert (span["input_tokens"], span["output_tokens"], span["cost_usd"]) == (None, None, None)
