"""``wrap_openai`` records the service tier, web searches and why a stream has no usage.

The real ``openai`` SDK runs on a mocked transport, as in ``test_openai.py``.
"""

from __future__ import annotations

import openai
import pytest
from provider_mocks import (
    OPENAI_CHAT_COMPLETION,
    MockProvider,
    json_response,
    openai_chunk,
    openai_responses_stream,
    openai_text_stream,
    openai_web_search_response,
    openai_web_search_stream,
    sse_response,
    sse_response_cut_by_transport_error,
)
from support import flushed_spans

import spanlight

API_KEY = "sk-proj-test-secret-key-0123456789"
MESSAGES = [{"role": "user", "content": "Where is my order?"}]


def make_client(provider: MockProvider, tracer: spanlight.Spanlight) -> openai.OpenAI:
    client = openai.OpenAI(api_key=API_KEY, http_client=provider.sync_client(), max_retries=0)
    return spanlight.wrap_openai(client, spanlight=tracer)


def test_chat_service_tier_flex_recorded(tracer, ingest):
    body = {**OPENAI_CHAT_COMPLETION, "service_tier": "flex"}
    client = make_client(MockProvider(json_response(body)), tracer)

    client.chat.completions.create(model="gpt-4o-mini", messages=MESSAGES, service_tier="flex")

    span = flushed_spans(tracer, ingest)[0]
    assert span["usage"]["service_tier"] == "flex"
    assert "usage.service_tier_raw" not in span["attributes"]


def test_chat_stream_reads_the_last_chunk_tier(tracer, ingest):
    events = openai_text_stream(include_usage=True)
    name, last = events[-2]
    assert isinstance(last, dict)
    events[-2] = (name, {**last, "service_tier": "flex"})
    client = make_client(MockProvider(sse_response(events)), tracer)

    stream = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=MESSAGES,
        stream=True,
        stream_options={"include_usage": True},
    )
    for _ in stream:
        pass

    span = flushed_spans(tracer, ingest)[0]
    assert span["usage"]["service_tier"] == "flex"


def test_responses_service_tier_priority(tracer, ingest):
    client = make_client(MockProvider(json_response(openai_web_search_response())), tracer)

    client.responses.create(model="gpt-4.1", input="Where is my order?", service_tier="priority")

    span = flushed_spans(tracer, ingest)[0]
    # Recorded as reported; the server prices an OpenAI priority call at the standard rate.
    assert span["usage"]["service_tier"] == "priority"


def test_responses_unknown_tier_kept_raw(tracer, ingest):
    body = openai_web_search_response(service_tier="ultrafast")
    client = make_client(MockProvider(json_response(body)), tracer)

    client.responses.create(model="gpt-4.1", input="Where is my order?")

    span = flushed_spans(tracer, ingest)[0]
    assert "service_tier" not in span["usage"]
    assert span["attributes"]["usage.service_tier_raw"] == "ultrafast"


def test_responses_web_search_calls_counted(tracer, ingest):
    client = make_client(MockProvider(json_response(openai_web_search_response())), tracer)

    response = client.responses.create(
        model="gpt-4.1", input="Where is my order?", tools=[{"type": "web_search"}]
    )

    assert len(response.output) == 4
    span = flushed_spans(tracer, ingest)[0]
    # Two completed searches; the one still in progress is not billed.
    assert span["usage"]["server_tool_use"] == {"web_search_requests": 2}
    assert span["usage"]["input_tokens"] == 36


def test_responses_without_searches_records_no_count(tracer, ingest):
    body = {**openai_web_search_response(), "output": openai_web_search_response()["output"][3:]}
    client = make_client(MockProvider(json_response(body)), tracer)

    client.responses.create(model="gpt-4.1", input="Where is my order?")

    span = flushed_spans(tracer, ingest)[0]
    assert "server_tool_use" not in span["usage"]


def test_responses_stream_counts_searches_from_terminal_event(tracer, ingest):
    client = make_client(MockProvider(sse_response(openai_web_search_stream())), tracer)

    for _ in client.responses.create(model="gpt-4.1", input="Where is my order?", stream=True):
        pass

    span = flushed_spans(tracer, ingest)[0]
    assert span["usage"]["server_tool_use"] == {"web_search_requests": 2}
    assert span["usage"]["service_tier"] == "priority"


def test_chat_web_search_options_sets_fee_unknown(tracer, ingest):
    client = make_client(MockProvider(json_response(OPENAI_CHAT_COMPLETION)), tracer)

    client.chat.completions.create(
        model="gpt-4o-search-preview", messages=MESSAGES, web_search_options={}
    )
    client.chat.completions.create(model="gpt-4o-mini", messages=MESSAGES)

    with_search, without_search = flushed_spans(tracer, ingest)
    # Annotations are not a call count, so no count is recorded and the fee is flagged unknown.
    assert with_search["attributes"]["usage.fee_unknown"] == "openai_chat_web_search"
    assert "server_tool_use" not in with_search["usage"]
    assert "usage.fee_unknown" not in without_search["attributes"]


def test_stream_without_usage_sets_unreported_reason(tracer, ingest):
    provider = MockProvider(sse_response(openai_text_stream(include_usage=False)))
    client = make_client(provider, tracer)

    for _ in client.chat.completions.create(model="gpt-4o-mini", messages=MESSAGES, stream=True):
        pass

    span = flushed_spans(tracer, ingest)[0]
    assert span["attributes"]["usage.unreported_reason"] == "include_usage_not_set"
    assert "usage.missing_reason" not in span["attributes"]
    assert "output_tokens" not in span["usage"]
    assert span["usage"]["service_tier"] == "standard"


def test_stream_closed_early_is_client_disconnected(tracer, ingest):
    provider = MockProvider(sse_response(openai_text_stream(include_usage=True)))
    client = make_client(provider, tracer)

    stream = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=MESSAGES,
        stream=True,
        stream_options={"include_usage": True},
    )
    next(iter(stream))
    stream.close()

    span = flushed_spans(tracer, ingest)[0]
    assert span["attributes"]["usage.unreported_reason"] == "client_disconnected"
    assert "output_tokens" not in (span["usage"] or {})


def test_stream_error_is_stream_error(tracer, ingest):
    events = [
        (None, openai_chunk({"role": "assistant", "content": "Hel"})),
        (None, {"error": {"message": "The server had an error", "type": "server_error"}}),
    ]
    client = make_client(MockProvider(sse_response(events)), tracer)

    stream = client.chat.completions.create(model="gpt-4o-mini", messages=MESSAGES, stream=True)
    try:
        for _ in stream:
            pass
    except openai.APIError:
        pass

    span = flushed_spans(tracer, ingest)[0]
    assert span["status"] == "error"
    assert span["attributes"]["usage.unreported_reason"] == "stream_error"


def test_responses_stream_closed_early_is_client_disconnected(tracer, ingest):
    client = make_client(MockProvider(sse_response(openai_responses_stream())), tracer)

    stream = client.responses.create(model="gpt-4.1", input="Where is my order?", stream=True)
    next(iter(stream))
    stream.close()

    span = flushed_spans(tracer, ingest)[0]
    assert span["attributes"]["usage.unreported_reason"] == "client_disconnected"
    assert "output_tokens" not in (span["usage"] or {})


def test_responses_stream_without_terminal_event_is_no_final_usage(tracer, ingest):
    events = openai_responses_stream()[:-1]
    client = make_client(MockProvider(sse_response(events)), tracer)

    for _ in client.responses.create(model="gpt-4.1", input="Where is my order?", stream=True):
        pass

    span = flushed_spans(tracer, ingest)[0]
    assert span["status"] == "ok"
    assert span["attributes"]["usage.unreported_reason"] == "no_final_usage"
    assert span["output"]["content"] == "Your order has shipped."
    assert "output_tokens" not in (span["usage"] or {})


def test_responses_stream_cut_is_stream_error(tracer, ingest):
    events = openai_responses_stream()[:-1]
    client = make_client(MockProvider(sse_response_cut_by_transport_error(events)), tracer)

    stream = client.responses.create(model="gpt-4.1", input="Where is my order?", stream=True)
    with pytest.raises(Exception):  # noqa: B017 - the provider httpx ReadError
        for _ in stream:
            pass

    span = flushed_spans(tracer, ingest)[0]
    assert span["status"] == "error"
    assert span["attributes"]["usage.unreported_reason"] == "stream_error"


def test_responses_stream_completed_sets_no_reason(tracer, ingest):
    client = make_client(MockProvider(sse_response(openai_responses_stream())), tracer)

    for _ in client.responses.create(model="gpt-4.1", input="Where is my order?", stream=True):
        pass

    span = flushed_spans(tracer, ingest)[0]
    assert "usage.unreported_reason" not in span["attributes"]
    assert span["usage"]["output_tokens"] == 6
