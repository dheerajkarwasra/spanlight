"""Anthropic streams that end before the provider's final usage event.

Such a span must say that the output count is unknown (``output_tokens``
absent plus ``usage.unreported_reason``), never carry the ``output_tokens: 1``
placeholder from ``message_start``, and still keep the prompt counts that
``message_start`` reported.
"""

from __future__ import annotations

import anthropic
import pytest
from provider_mocks import (
    MockProvider,
    anthropic_stream_cut_after_delta,
    anthropic_text_stream,
    provider_httpx,
    sse_response,
    sse_response_cut_by_transport_error,
)
from support import flushed_spans

import spanlight

API_KEY = "sk-ant-api03-test-secret"
MODEL = "claude-haiku-4-5"
MESSAGES = [{"role": "user", "content": "How long do refunds take?"}]

# What message_start reported, with the placeholder output count dropped.
INPUT_ONLY_USAGE = {"input_tokens": 25, "cached_tokens": 0, "cache_write_tokens": 0}


def make_client(provider: MockProvider, tracer: spanlight.Spanlight) -> anthropic.Anthropic:
    client = anthropic.Anthropic(api_key=API_KEY, http_client=provider.sync_client(), max_retries=0)
    return spanlight.wrap_anthropic(client, spanlight=tracer)


def make_async_client(
    provider: MockProvider, tracer: spanlight.Spanlight
) -> anthropic.AsyncAnthropic:
    client = anthropic.AsyncAnthropic(
        api_key=API_KEY, http_client=provider.async_client(), max_retries=0
    )
    return spanlight.wrap_anthropic(client, spanlight=tracer)


def test_stream_closed_after_delta_has_no_output_tokens_and_reason(tracer, ingest):
    provider = MockProvider(sse_response(anthropic_text_stream()))
    client = make_client(provider, tracer)

    stream = client.messages.create(model=MODEL, max_tokens=256, messages=MESSAGES, stream=True)
    for event in stream:
        if event.type == "content_block_delta":
            break
    stream.close()

    span = flushed_spans(tracer, ingest)[0]
    assert span["status"] == "ok"
    assert span["usage"] == INPUT_ONLY_USAGE
    assert span["attributes"]["usage.unreported_reason"] == "client_disconnected"
    assert span["output"]["content"] == [{"type": "text", "text": "Hi"}]


async def test_async_stream_closed_after_delta_has_no_output_tokens_and_reason(tracer, ingest):
    provider = MockProvider(sse_response(anthropic_text_stream()))
    client = make_async_client(provider, tracer)

    stream = await client.messages.create(
        model=MODEL, max_tokens=256, messages=MESSAGES, stream=True
    )
    async for event in stream:
        if event.type == "content_block_delta":
            break
    await stream.close()

    span = flushed_spans(tracer, ingest)[0]
    assert span["usage"] == INPUT_ONLY_USAGE
    assert span["attributes"]["usage.unreported_reason"] == "client_disconnected"


def test_stream_helper_exited_early_is_client_disconnected(tracer, ingest):
    provider = MockProvider(sse_response(anthropic_text_stream()))
    client = make_client(provider, tracer)

    with client.messages.stream(model=MODEL, max_tokens=256, messages=MESSAGES) as stream:
        next(iter(stream.text_stream))

    span = flushed_spans(tracer, ingest)[0]
    assert span["status"] == "ok"
    assert span["usage"] == INPUT_ONLY_USAGE
    assert span["attributes"]["usage.unreported_reason"] == "client_disconnected"


async def test_async_stream_helper_exited_early_is_client_disconnected(tracer, ingest):
    provider = MockProvider(sse_response(anthropic_text_stream()))
    client = make_async_client(provider, tracer)

    async with client.messages.stream(model=MODEL, max_tokens=256, messages=MESSAGES) as stream:
        async for _ in stream.text_stream:
            break

    span = flushed_spans(tracer, ingest)[0]
    assert span["status"] == "ok"
    assert span["usage"] == INPUT_ONLY_USAGE
    assert span["attributes"]["usage.unreported_reason"] == "client_disconnected"


def test_transport_error_mid_stream_is_stream_error(tracer, ingest):
    provider = MockProvider(sse_response_cut_by_transport_error(anthropic_stream_cut_after_delta()))
    client = make_client(provider, tracer)

    received: list[str] = []
    with pytest.raises(provider_httpx.ReadError):
        for event in client.messages.create(
            model=MODEL, max_tokens=256, messages=MESSAGES, stream=True
        ):
            received.append(event.type)

    assert "content_block_delta" in received
    span = flushed_spans(tracer, ingest)[0]
    assert span["status"] == "error"
    assert span["usage"] == INPUT_ONLY_USAGE
    assert span["attributes"]["usage.unreported_reason"] == "stream_error"
    assert span["output"]["content"] == [{"type": "text", "text": "Hi"}]


async def test_async_transport_error_mid_stream_is_stream_error(tracer, ingest):
    provider = MockProvider(sse_response_cut_by_transport_error(anthropic_stream_cut_after_delta()))
    client = make_async_client(provider, tracer)

    with pytest.raises(provider_httpx.ReadError):
        stream = await client.messages.create(
            model=MODEL, max_tokens=256, messages=MESSAGES, stream=True
        )
        async for _ in stream:
            pass

    span = flushed_spans(tracer, ingest)[0]
    assert span["status"] == "error"
    assert span["usage"] == INPUT_ONLY_USAGE
    assert span["attributes"]["usage.unreported_reason"] == "stream_error"


def test_stream_ending_after_message_start_is_no_final_usage(tracer, ingest):
    provider = MockProvider(sse_response(anthropic_text_stream()[:1]))
    client = make_client(provider, tracer)

    events = list(
        client.messages.create(model=MODEL, max_tokens=256, messages=MESSAGES, stream=True)
    )

    assert [event.type for event in events] == ["message_start"]
    span = flushed_spans(tracer, ingest)[0]
    assert span["status"] == "ok"
    assert span["model"] == "claude-haiku-4-5-20251001"
    assert span["usage"] == INPUT_ONLY_USAGE
    assert span["attributes"]["usage.unreported_reason"] == "no_final_usage"


async def test_async_stream_ending_after_message_start_is_no_final_usage(tracer, ingest):
    provider = MockProvider(sse_response(anthropic_text_stream()[:1]))
    client = make_async_client(provider, tracer)

    stream = await client.messages.create(
        model=MODEL, max_tokens=256, messages=MESSAGES, stream=True
    )
    events = [event async for event in stream]

    assert [event.type for event in events] == ["message_start"]
    span = flushed_spans(tracer, ingest)[0]
    assert span["status"] == "ok"
    assert span["model"] == "claude-haiku-4-5-20251001"
    assert span["usage"] == INPUT_ONLY_USAGE
    assert span["attributes"]["usage.unreported_reason"] == "no_final_usage"


def test_stream_ending_after_delta_is_no_final_usage(tracer, ingest):
    provider = MockProvider(sse_response(anthropic_stream_cut_after_delta()))
    client = make_client(provider, tracer)

    for _ in client.messages.create(model=MODEL, max_tokens=256, messages=MESSAGES, stream=True):
        pass

    span = flushed_spans(tracer, ingest)[0]
    assert span["usage"] == INPUT_ONLY_USAGE
    assert span["attributes"]["usage.unreported_reason"] == "no_final_usage"
    assert span["output"]["content"] == [{"type": "text", "text": "Hi"}]


async def test_async_stream_ending_after_delta_is_no_final_usage(tracer, ingest):
    provider = MockProvider(sse_response(anthropic_stream_cut_after_delta()))
    client = make_async_client(provider, tracer)

    stream = await client.messages.create(
        model=MODEL, max_tokens=256, messages=MESSAGES, stream=True
    )
    async for _ in stream:
        pass

    span = flushed_spans(tracer, ingest)[0]
    assert span["usage"] == INPUT_ONLY_USAGE
    assert span["attributes"]["usage.unreported_reason"] == "no_final_usage"


def test_complete_stream_unchanged(tracer, ingest):
    provider = MockProvider(sse_response(anthropic_text_stream()))
    client = make_client(provider, tracer)

    with client.messages.stream(model=MODEL, max_tokens=256, messages=MESSAGES) as stream:
        stream.get_final_message()

    span = flushed_spans(tracer, ingest)[0]
    assert span["usage"]["output_tokens"] == 15
    assert "usage.unreported_reason" not in span["attributes"]
