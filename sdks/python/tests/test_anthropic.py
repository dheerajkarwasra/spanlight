"""``wrap_anthropic`` against the real ``anthropic`` SDK on a mocked HTTP transport."""

from __future__ import annotations

import json

import anthropic
import pytest
from provider_mocks import (
    ANTHROPIC_CACHE_WRITE_MESSAGE,
    ANTHROPIC_INVALID_REQUEST,
    ANTHROPIC_MESSAGE,
    MockProvider,
    anthropic_cache_write_stream,
    anthropic_stream_with_overload_error,
    anthropic_text_stream,
    anthropic_tool_use_stream,
    json_response,
    sse_response,
)
from support import flushed_spans

import spanlight

API_KEY = "sk-ant-api03-test-secret"
MODEL = "claude-haiku-4-5"
MESSAGES = [{"role": "user", "content": "How long do refunds take?"}]


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


def test_messages_create_span_fields(tracer, ingest):
    provider = MockProvider(json_response(ANTHROPIC_MESSAGE))
    client = make_client(provider, tracer)

    with tracer.span("support-answer") as parent:
        message = client.messages.create(
            model=MODEL,
            max_tokens=256,
            system="You are a support agent.",
            messages=MESSAGES,
        )

    assert message.content[0].text == "Refunds take 5-7 business days."
    flushed_spans(tracer, ingest)
    span = ingest.span_named(f"chat {MODEL}")

    assert span["kind"] == "llm"
    assert span["status"] == "ok"
    assert span["parent_span_id"] == parent.span_id
    assert span["provider"] == "anthropic"
    assert span["model"] == "claude-haiku-4-5-20251001"
    # input_tokens is the full prompt: 12 uncached + 1500 cache reads + 3 cache writes.
    # No TTL split was reported, so cache_write_1h_tokens is omitted (all writes 5-minute).
    assert span["usage"] == {
        "input_tokens": 1515,
        "output_tokens": 9,
        "cached_tokens": 1500,
        "cache_write_tokens": 3,
    }
    assert "usage.cache_creation_input_tokens" not in span["attributes"]
    assert span["attributes"]["response.stop_reason"] == "end_turn"
    assert span["attributes"]["response.id"] == "msg_01XFDUDYJgAACzvnptvVoYEL"
    assert span["input"] == {
        "model": MODEL,
        "max_tokens": 256,
        "system": "You are a support agent.",
        "messages": MESSAGES,
    }
    assert span["output"] == {
        "role": "assistant",
        "content": [{"type": "text", "text": "Refunds take 5-7 business days."}],
    }
    assert API_KEY not in json.dumps(span)


# Real 2026-10-14 pilot usage: 19 uncached + 0 reads + 6404 writes, all 1-hour TTL.
CACHE_WRITE_USAGE = {
    "input_tokens": 6423,
    "output_tokens": 12,
    "cached_tokens": 0,
    "cache_write_tokens": 6404,
    "cache_write_1h_tokens": 6404,
}


def test_create_records_cache_write_fields(tracer, ingest):
    provider = MockProvider(json_response(ANTHROPIC_CACHE_WRITE_MESSAGE))
    client = make_client(provider, tracer)

    message = client.messages.create(model=MODEL, max_tokens=256, messages=MESSAGES)

    assert message.usage.cache_creation.ephemeral_1h_input_tokens == 6404
    span = flushed_spans(tracer, ingest)[0]
    assert span["usage"] == CACHE_WRITE_USAGE
    assert "usage.cache_creation_input_tokens" not in span["attributes"]


def test_stream_cache_write_fields(tracer, ingest):
    provider = MockProvider(sse_response(anthropic_cache_write_stream()))
    client = make_client(provider, tracer)

    with client.messages.stream(model=MODEL, max_tokens=256, messages=MESSAGES) as stream:
        streamed_text = "".join(stream.text_stream)

    assert streamed_text == "Hi, how can I help?"
    span = flushed_spans(tracer, ingest)[0]
    assert span["usage"] == CACHE_WRITE_USAGE
    assert "usage.cache_creation_input_tokens" not in span["attributes"]


async def test_async_messages_create(tracer, ingest):
    provider = MockProvider(json_response(ANTHROPIC_MESSAGE))
    client = make_async_client(provider, tracer)

    message = await client.messages.create(model=MODEL, max_tokens=256, messages=MESSAGES)

    assert message.stop_reason == "end_turn"
    span = flushed_spans(tracer, ingest)[0]
    assert span["model"] == "claude-haiku-4-5-20251001"
    assert span["usage"]["output_tokens"] == 9


def test_create_with_stream_true(tracer, ingest):
    provider = MockProvider(sse_response(anthropic_text_stream()))
    client = make_client(provider, tracer)

    stream = client.messages.create(model=MODEL, max_tokens=256, messages=MESSAGES, stream=True)
    text = "".join(
        event.delta.text
        for event in stream
        if event.type == "content_block_delta" and event.delta.type == "text_delta"
    )

    assert text == "Hi, how can I help?"
    span = flushed_spans(tracer, ingest)[0]
    assert span["status"] == "ok"
    assert span["model"] == "claude-haiku-4-5-20251001"
    assert span["usage"] == {
        "input_tokens": 25,
        "output_tokens": 15,
        "cached_tokens": 0,
        "cache_write_tokens": 0,
    }
    assert span["time_to_first_token_ms"] is not None
    assert span["output"] == {
        "role": "assistant",
        "content": [{"type": "text", "text": "Hi, how can I help?"}],
    }
    assert span["attributes"]["request.stream"] is True


async def test_async_create_with_stream_true(tracer, ingest):
    provider = MockProvider(sse_response(anthropic_text_stream()))
    client = make_async_client(provider, tracer)

    stream = await client.messages.create(
        model=MODEL, max_tokens=256, messages=MESSAGES, stream=True
    )
    event_types = [event.type async for event in stream]

    assert event_types[0] == "message_start"
    assert event_types[-1] == "message_stop"
    span = flushed_spans(tracer, ingest)[0]
    assert span["output"]["content"][0]["text"] == "Hi, how can I help?"
    assert span["time_to_first_token_ms"] is not None


def test_messages_stream_helper(tracer, ingest):
    provider = MockProvider(sse_response(anthropic_text_stream()))
    client = make_client(provider, tracer)

    with client.messages.stream(model=MODEL, max_tokens=256, messages=MESSAGES) as stream:
        streamed_text = "".join(stream.text_stream)
        final = stream.get_final_message()

    assert streamed_text == "Hi, how can I help?"
    assert final.usage.output_tokens == 15
    span = flushed_spans(tracer, ingest)[0]
    assert span["name"] == f"chat {MODEL}"
    assert span["usage"] == {
        "input_tokens": 25,
        "output_tokens": 15,
        "cached_tokens": 0,
        "cache_write_tokens": 0,
    }
    assert span["time_to_first_token_ms"] is not None
    assert span["output"]["content"] == [{"type": "text", "text": "Hi, how can I help?"}]
    assert span["input"]["stream"] is True


async def test_async_messages_stream_helper(tracer, ingest):
    provider = MockProvider(sse_response(anthropic_text_stream()))
    client = make_async_client(provider, tracer)

    async with client.messages.stream(model=MODEL, max_tokens=256, messages=MESSAGES) as stream:
        pieces = [text async for text in stream.text_stream]

    assert "".join(pieces) == "Hi, how can I help?"
    span = flushed_spans(tracer, ingest)[0]
    assert span["status"] == "ok"
    assert span["usage"]["output_tokens"] == 15
    assert span["time_to_first_token_ms"] is not None


def test_stream_helper_exited_early_still_ends_span(tracer, ingest):
    provider = MockProvider(sse_response(anthropic_text_stream()))
    client = make_client(provider, tracer)

    with client.messages.stream(model=MODEL, max_tokens=256, messages=MESSAGES) as stream:
        next(iter(stream.text_stream))

    span = flushed_spans(tracer, ingest)[0]
    assert span["status"] == "ok"
    assert span["output"]["content"][0]["text"] == "Hi"


def test_streamed_tool_use_input_is_parsed(tracer, ingest):
    provider = MockProvider(sse_response(anthropic_tool_use_stream()))
    client = make_client(provider, tracer)

    for _ in client.messages.create(model=MODEL, max_tokens=256, messages=MESSAGES, stream=True):
        pass

    span = flushed_spans(tracer, ingest)[0]
    assert span["output"]["content"] == [
        {
            "type": "tool_use",
            "id": "toolu_01T1x1fJ34qAmk2tNTrN7Up6",
            "name": "get_order",
            "input": {"order_id": "42"},
        }
    ]
    assert span["attributes"]["response.stop_reason"] == "tool_use"


def test_api_error_is_recorded_and_reraised_unchanged(tracer, ingest):
    provider = MockProvider(json_response(ANTHROPIC_INVALID_REQUEST, status=400))
    client = make_client(provider, tracer)

    with pytest.raises(anthropic.BadRequestError) as raised:
        client.messages.create(model=MODEL, max_tokens=256, messages=MESSAGES)

    assert raised.value.status_code == 400
    span = flushed_spans(tracer, ingest)[0]
    assert span["status"] == "error"
    assert "max_tokens: Field required" in span["status_message"]
    assert span["attributes"]["error.type"] == "BadRequestError"
    assert span["attributes"]["http.status_code"] == 400


def test_error_event_mid_stream_marks_span_failed(tracer, ingest):
    provider = MockProvider(sse_response(anthropic_stream_with_overload_error()))
    client = make_client(provider, tracer)

    received: list[str] = []
    with pytest.raises(anthropic.APIStatusError, match="Overloaded"):
        for event in client.messages.create(
            model=MODEL, max_tokens=256, messages=MESSAGES, stream=True
        ):
            received.append(event.type)

    assert "content_block_delta" in received
    span = flushed_spans(tracer, ingest)[0]
    assert span["status"] == "error"
    assert "Overloaded" in span["status_message"]
    assert span["output"]["content"][0]["text"] == "Hi"  # partial output is kept


def test_stream_helper_request_failure(tracer, ingest):
    provider = MockProvider(json_response(ANTHROPIC_INVALID_REQUEST, status=400))
    client = make_client(provider, tracer)

    with (
        pytest.raises(anthropic.BadRequestError),
        client.messages.stream(model=MODEL, max_tokens=256, messages=MESSAGES),
    ):
        pass

    span = flushed_spans(tracer, ingest)[0]
    assert span["status"] == "error"
    assert span["attributes"]["http.status_code"] == 400


def test_disabled_sdk_leaves_client_behaviour_untouched(ingest):
    tracer = spanlight.Spanlight(enabled=False)
    provider = MockProvider(sse_response(anthropic_text_stream()))
    client = make_client(provider, tracer)

    stream = client.messages.create(model=MODEL, max_tokens=256, messages=MESSAGES, stream=True)

    assert isinstance(stream, anthropic.Stream)
    assert [event.type for event in stream][-1] == "message_stop"
    assert ingest.requests == []
