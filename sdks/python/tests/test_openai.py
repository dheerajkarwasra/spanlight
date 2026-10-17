"""``wrap_openai`` against the real ``openai`` SDK on a mocked HTTP transport."""

from __future__ import annotations

import json

import openai
import pytest
from provider_mocks import (
    OPENAI_CHAT_COMPLETION,
    OPENAI_RATE_LIMIT_ERROR,
    OPENAI_TOOL_CALL_STREAM,
    MockProvider,
    json_response,
    openai_response_object,
    openai_responses_stream,
    openai_text_stream,
    sse_response,
)
from support import flushed_spans

import spanlight

API_KEY = "sk-proj-test-secret-key-0123456789"
MESSAGES = [
    {"role": "system", "content": "You are a support agent."},
    {"role": "user", "content": "How do refunds work?"},
]


def make_client(provider: MockProvider, tracer: spanlight.Spanlight) -> openai.OpenAI:
    client = openai.OpenAI(api_key=API_KEY, http_client=provider.sync_client(), max_retries=0)
    return spanlight.wrap_openai(client, spanlight=tracer)


def make_async_client(provider: MockProvider, tracer: spanlight.Spanlight) -> openai.AsyncOpenAI:
    client = openai.AsyncOpenAI(api_key=API_KEY, http_client=provider.async_client(), max_retries=0)
    return spanlight.wrap_openai(client, spanlight=tracer)


# ---------------------------------------------------------------------- #
# Chat Completions
# ---------------------------------------------------------------------- #


def test_chat_completion_span_fields(tracer, ingest):
    provider = MockProvider(json_response(OPENAI_CHAT_COMPLETION))
    client = make_client(provider, tracer)

    with tracer.span("answer") as parent:
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=MESSAGES,
            temperature=0.2,
            extra_headers={"X-Custom-Auth": "Bearer super-secret"},
        )

    assert completion.choices[0].message.content == "You can request a refund within 30 days."
    flushed_spans(tracer, ingest)
    span = ingest.span_named("chat gpt-4o-mini")

    assert span["kind"] == "llm"
    assert span["status"] == "ok"
    assert span["parent_span_id"] == parent.span_id
    assert span["provider"] == "openai"
    assert span["model"] == "gpt-4o-mini-2024-07-18"  # response model wins
    assert span["usage"] == {
        "input_tokens": 1243,
        "output_tokens": 11,
        "cached_tokens": 1024,
        "service_tier": "standard",  # OpenAI answered "default"
    }
    assert span["time_to_first_token_ms"] is None
    assert span["input"] == {"model": "gpt-4o-mini", "messages": MESSAGES, "temperature": 0.2}
    assert span["output"] == {
        "role": "assistant",
        "content": "You can request a refund within 30 days.",
        "annotations": [],
    }
    assert span["attributes"]["response.id"] == "chatcmpl-B9MBs8CjcvOU2jLn4n570S5qMJKcT"
    assert span["attributes"]["response.finish_reasons"] == ["stop"]


def test_secrets_never_reach_the_payload(tracer, ingest):
    provider = MockProvider(json_response(OPENAI_CHAT_COMPLETION))
    client = make_client(provider, tracer)

    client.chat.completions.create(
        model="gpt-4o-mini",
        messages=MESSAGES,
        extra_headers={"X-Proxy-Token": "header-secret"},
    )

    serialized = json.dumps(flushed_spans(tracer, ingest))
    assert API_KEY not in serialized
    assert "header-secret" not in serialized
    # The provider still received the real key: instrumentation is transparent.
    assert provider.requests[0].headers["Authorization"] == f"Bearer {API_KEY}"


async def test_async_chat_completion(tracer, ingest):
    provider = MockProvider(json_response(OPENAI_CHAT_COMPLETION))
    client = make_async_client(provider, tracer)

    completion = await client.chat.completions.create(model="gpt-4o-mini", messages=MESSAGES)

    assert completion.usage is not None
    span = flushed_spans(tracer, ingest)[0]
    assert span["model"] == "gpt-4o-mini-2024-07-18"
    assert span["usage"]["input_tokens"] == 1243


def test_streaming_with_include_usage(tracer, ingest):
    provider = MockProvider(sse_response(openai_text_stream(include_usage=True)))
    client = make_client(provider, tracer)

    stream = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=MESSAGES,
        stream=True,
        stream_options={"include_usage": True},
    )
    text = "".join(chunk.choices[0].delta.content or "" for chunk in stream if chunk.choices)

    assert text == "Hello there!"
    span = flushed_spans(tracer, ingest)[0]
    assert span["status"] == "ok"
    assert span["model"] == "gpt-4o-mini-2024-07-18"
    assert span["usage"] == {
        "input_tokens": 20,
        "output_tokens": 3,
        "cached_tokens": 0,
        "service_tier": "standard",
    }
    assert span["time_to_first_token_ms"] is not None
    assert span["time_to_first_token_ms"] >= 0
    assert span["output"] == {"role": "assistant", "content": "Hello there!"}
    assert span["attributes"]["request.stream"] is True
    assert span["attributes"]["response.finish_reasons"] == ["stop"]


def test_streaming_without_usage_records_why(tracer, ingest):
    provider = MockProvider(sse_response(openai_text_stream(include_usage=False)))
    client = make_client(provider, tracer)

    with client.chat.completions.create(
        model="gpt-4o-mini", messages=MESSAGES, stream=True
    ) as stream:
        for _ in stream:
            pass

    span = flushed_spans(tracer, ingest)[0]
    # The chunks carry the tier but no token counts.
    assert span["usage"] == {"service_tier": "standard"}
    assert span["attributes"]["usage.unreported_reason"] == "include_usage_not_set"
    assert span["output"]["content"] == "Hello there!"


def test_streaming_span_waits_for_the_stream_to_finish(tracer, ingest):
    provider = MockProvider(sse_response(openai_text_stream(include_usage=True)))
    client = make_client(provider, tracer)

    stream = client.chat.completions.create(model="gpt-4o-mini", messages=MESSAGES, stream=True)
    next(iter(stream))
    assert tracer.flush(timeout=5)
    assert ingest.spans == []

    stream.close()
    assert len(flushed_spans(tracer, ingest)) == 1


def test_streamed_tool_calls_are_reassembled(tracer, ingest):
    provider = MockProvider(sse_response(OPENAI_TOOL_CALL_STREAM))
    client = make_client(provider, tracer)

    for _ in client.chat.completions.create(model="gpt-4o-mini", messages=MESSAGES, stream=True):
        pass

    span = flushed_spans(tracer, ingest)[0]
    assert span["output"]["tool_calls"] == [
        {
            "id": "call_abc123",
            "type": "function",
            "function": {"name": "get_order", "arguments": '{"order_id": "42"}'},
        }
    ]
    assert span["attributes"]["response.finish_reasons"] == ["tool_calls"]
    assert span["time_to_first_token_ms"] is not None


async def test_async_streaming(tracer, ingest):
    provider = MockProvider(sse_response(openai_text_stream(include_usage=True)))
    client = make_async_client(provider, tracer)

    stream = await client.chat.completions.create(
        model="gpt-4o-mini",
        messages=MESSAGES,
        stream=True,
        stream_options={"include_usage": True},
    )
    pieces = [chunk.choices[0].delta.content async for chunk in stream if chunk.choices]

    assert "".join(piece or "" for piece in pieces) == "Hello there!"
    span = flushed_spans(tracer, ingest)[0]
    assert span["usage"]["output_tokens"] == 3
    assert span["time_to_first_token_ms"] is not None


def test_stream_helper_is_traced_through_create(tracer, ingest):
    provider = MockProvider(sse_response(openai_text_stream(include_usage=True)))
    client = make_client(provider, tracer)

    with client.chat.completions.stream(model="gpt-4o-mini", messages=MESSAGES) as stream:
        final = stream.get_final_completion()

    assert final.choices[0].message.content == "Hello there!"
    span = flushed_spans(tracer, ingest)[0]
    assert span["output"]["content"] == "Hello there!"
    assert span["usage"]["input_tokens"] == 20


def test_api_error_is_recorded_and_reraised_unchanged(tracer, ingest):
    provider = MockProvider(json_response(OPENAI_RATE_LIMIT_ERROR, status=429))
    client = make_client(provider, tracer)

    with pytest.raises(openai.RateLimitError) as raised:
        client.chat.completions.create(model="gpt-4o-mini", messages=MESSAGES)

    assert raised.value.status_code == 429
    span = flushed_spans(tracer, ingest)[0]
    assert span["status"] == "error"
    assert "Rate limit reached" in span["status_message"]
    assert span["attributes"]["error.type"] == "RateLimitError"
    assert span["attributes"]["http.status_code"] == 429
    assert span["model"] == "gpt-4o-mini"  # request model when no response


def test_with_raw_response_passes_through_untraced(tracer, ingest):
    provider = MockProvider(json_response(OPENAI_CHAT_COMPLETION))
    client = make_client(provider, tracer)

    raw = client.chat.completions.with_raw_response.create(model="gpt-4o-mini", messages=MESSAGES)

    assert raw.parse().model == "gpt-4o-mini-2024-07-18"
    assert flushed_spans(tracer, ingest) == []


def test_wrapping_twice_records_one_span(tracer, ingest):
    provider = MockProvider(json_response(OPENAI_CHAT_COMPLETION))
    client = make_client(provider, tracer)
    spanlight.wrap_openai(client, spanlight=tracer)

    client.chat.completions.create(model="gpt-4o-mini", messages=MESSAGES)

    assert len(flushed_spans(tracer, ingest)) == 1


def test_wrapper_uses_global_client_by_default(global_tracer, ingest):
    provider = MockProvider(json_response(OPENAI_CHAT_COMPLETION))
    client = spanlight.wrap_openai(
        openai.OpenAI(api_key=API_KEY, http_client=provider.sync_client(), max_retries=0)
    )

    client.chat.completions.create(model="gpt-4o-mini", messages=MESSAGES)

    assert flushed_spans(global_tracer, ingest)[0]["provider"] == "openai"


# ---------------------------------------------------------------------- #
# Responses API
# ---------------------------------------------------------------------- #


def test_responses_create(tracer, ingest):
    provider = MockProvider(json_response(openai_response_object()))
    client = make_client(provider, tracer)

    response = client.responses.create(
        model="gpt-4.1",
        instructions="You are a support agent.",
        input="Where is my order?",
    )

    assert response.output_text == "Your order has shipped."
    flushed_spans(tracer, ingest)
    span = ingest.span_named("responses gpt-4.1")
    assert span["model"] == "gpt-4.1-2025-04-14"
    assert span["usage"] == {
        "input_tokens": 36,
        "output_tokens": 6,
        "cached_tokens": 8,
        "service_tier": "standard",
    }
    assert span["input"]["input"] == "Where is my order?"
    assert span["output"] == {"role": "assistant", "content": "Your order has shipped."}
    assert span["attributes"]["response.status"] == "completed"


def test_responses_streaming(tracer, ingest):
    provider = MockProvider(sse_response(openai_responses_stream()))
    client = make_client(provider, tracer)

    stream = client.responses.create(model="gpt-4.1", input="Where is my order?", stream=True)
    deltas = [event.delta for event in stream if event.type == "response.output_text.delta"]

    assert "".join(deltas) == "Your order has shipped."
    span = flushed_spans(tracer, ingest)[0]
    assert span["time_to_first_token_ms"] is not None
    assert span["usage"]["input_tokens"] == 36
    assert span["output"]["content"] == "Your order has shipped."


async def test_async_responses_create(tracer, ingest):
    provider = MockProvider(json_response(openai_response_object()))
    client = make_async_client(provider, tracer)

    response = await client.responses.create(model="gpt-4.1", input="Where is my order?")

    assert response.id.startswith("resp_")
    assert flushed_spans(tracer, ingest)[0]["usage"]["output_tokens"] == 6


# ---------------------------------------------------------------------- #
# Azure clients
# ---------------------------------------------------------------------- #


class AzureOpenAI(openai.OpenAI):
    """Stands in for ``openai.AzureOpenAI``: the wrapper recognises the client by class name."""


class AsyncAzureOpenAI(openai.AsyncOpenAI):
    """Stands in for ``openai.AsyncAzureOpenAI``."""


class MyAzureClient(AzureOpenAI):
    """A user subclass of an Azure client is still an Azure client."""


@pytest.mark.parametrize("client_type", [AzureOpenAI, MyAzureClient])
def test_azure_client_defaults_provider_to_azure_openai(tracer, ingest, client_type):
    # Azure bills its own rates, so its spans must not be recorded as `openai`.
    provider = MockProvider(json_response(OPENAI_CHAT_COMPLETION))
    client = client_type(api_key=API_KEY, http_client=provider.sync_client(), max_retries=0)
    spanlight.wrap_openai(client, spanlight=tracer)

    client.chat.completions.create(model="gpt-4o-mini", messages=MESSAGES)

    assert flushed_spans(tracer, ingest)[0]["provider"] == "azure_openai"


async def test_async_azure_client_defaults_provider_to_azure_openai(tracer, ingest):
    provider = MockProvider(json_response(OPENAI_CHAT_COMPLETION))
    client = AsyncAzureOpenAI(api_key=API_KEY, http_client=provider.async_client(), max_retries=0)
    spanlight.wrap_openai(client, spanlight=tracer)

    await client.chat.completions.create(model="gpt-4o-mini", messages=MESSAGES)

    assert flushed_spans(tracer, ingest)[0]["provider"] == "azure_openai"


def test_explicit_provider_wins_over_azure_detection(tracer, ingest):
    provider = MockProvider(json_response(OPENAI_CHAT_COMPLETION))
    client = AzureOpenAI(api_key=API_KEY, http_client=provider.sync_client(), max_retries=0)
    spanlight.wrap_openai(client, spanlight=tracer, provider="openai")

    client.chat.completions.create(model="gpt-4o-mini", messages=MESSAGES)

    assert flushed_spans(tracer, ingest)[0]["provider"] == "openai"
