"""Vercel AI SDK legacy mapper: one test per field source of the spec's `vercel_ai` table."""

import json

import pytest

from app.db.models.telemetry import SpanKind
from app.ingest.conventions.vercel_ai import map_vercel_ai


@pytest.mark.parametrize(
    ("sent", "expected"),
    [("anthropic.messages", "anthropic"), ("openai.chat", "openai"), ("openai", "openai")],
)
def test_provider_suffix_removed(sent: str, expected: str) -> None:
    assert map_vercel_ai({"ai.model.provider": sent}).provider == expected


def test_provider_falls_back_to_gen_ai_system() -> None:
    assert map_vercel_ai({"gen_ai.system": "openai"}).provider == "openai"


def test_model_precedence() -> None:
    attributes = {
        "ai.response.model": "gpt-4.1-2025-04-14",
        "gen_ai.response.model": "gpt-4.1-x",
        "ai.model.id": "gpt-4.1",
        "gen_ai.request.model": "gpt-4.1",
    }
    assert map_vercel_ai(attributes).model == "gpt-4.1-2025-04-14"
    del attributes["ai.response.model"]
    assert map_vercel_ai(attributes).model == "gpt-4.1-x"
    del attributes["gen_ai.response.model"]
    assert map_vercel_ai(attributes).model == "gpt-4.1"
    assert map_vercel_ai({"gen_ai.request.model": "m"}).model == "m"


def test_generate_text_usage_names() -> None:
    usage = map_vercel_ai(
        {"ai.usage.promptTokens": 12, "ai.usage.completionTokens": 7, "ai.usage.totalTokens": 19}
    ).usage
    assert (usage.input_tokens, usage.output_tokens) == (12, 7)
    assert usage.cached_tokens is None
    assert usage.cache_write_tokens is None


def test_stream_text_usage_names() -> None:
    usage = map_vercel_ai(
        {
            "ai.usage.inputTokens": 30,
            "ai.usage.outputTokens": 9,
            "ai.usage.cachedInputTokens": 10,
            "ai.usage.reasoningTokens": 4,
            "ai.usage.totalTokens": 39,
        }
    ).usage
    assert (usage.input_tokens, usage.output_tokens, usage.cached_tokens) == (30, 9, 10)


def test_seven_legacy_cache_details() -> None:
    usage = map_vercel_ai(
        {
            "ai.usage.inputTokens": 100,
            "ai.usage.inputTokenDetails.cacheReadTokens": 40,
            "ai.usage.inputTokenDetails.cacheWriteTokens": 20,
            "ai.usage.cachedInputTokens": 41,
        }
    ).usage
    assert (usage.cached_tokens, usage.cache_write_tokens) == (40, 20)


def test_ai_usage_beats_gen_ai_usage_on_the_same_span() -> None:
    usage = map_vercel_ai(
        {
            "ai.usage.inputTokens": 120,
            "gen_ai.usage.input_tokens": 100,
            "gen_ai.usage.output_tokens": 5,
        }
    ).usage
    assert usage.input_tokens == 120
    assert usage.output_tokens == 5


def test_reasoning_and_total_tokens_are_not_consumed() -> None:
    fields = map_vercel_ai({"ai.usage.reasoningTokens": 4, "ai.usage.totalTokens": 39})
    assert "ai.usage.reasoningTokens" not in fields.consumed_keys
    assert "ai.usage.totalTokens" not in fields.consumed_keys


def test_outer_span_prompt_and_response_text() -> None:
    prompt = {"system": "be brief", "prompt": "hi", "messages": None}
    fields = map_vercel_ai(
        {
            "ai.operationId": "ai.generateText",
            "ai.prompt": json.dumps(prompt),
            "ai.response.text": "hello",
            "ai.response.finishReason": "stop",
        }
    )
    assert fields.input == prompt
    assert fields.output == {"text": "hello"}
    assert fields.finish_reason_raw == "stop"
    assert fields.consumed_keys == frozenset({"ai.prompt", "ai.response.text"})


def test_inner_span_messages_and_tool_calls() -> None:
    messages = [{"role": "user", "content": "weather?"}]
    tool_calls = [{"toolName": "weather", "args": {"city": "Oslo"}}]
    fields = map_vercel_ai(
        {
            "ai.operationId": "ai.generateText.doGenerate",
            "ai.prompt.messages": json.dumps(messages),
            "ai.response.toolCalls": json.dumps(tool_calls),
            "ai.response.msToFirstChunk": 120.5,
        }
    )
    assert fields.input == messages
    assert fields.output == {"tool_calls": tool_calls}
    assert fields.time_to_first_token_ms == 120.5
    assert fields.kind is SpanKind.LLM


def test_generate_object_output() -> None:
    fields = map_vercel_ai({"ai.response.object": json.dumps({"city": "Oslo"})})
    assert fields.output == {"object": {"city": "Oslo"}}


def test_tool_span_args_and_result() -> None:
    fields = map_vercel_ai(
        {
            "ai.operationId": "ai.toolCall",
            "ai.toolCall.name": "weather",
            "ai.toolCall.args": json.dumps({"city": "Oslo"}),
            "ai.toolCall.result": json.dumps({"temp": 7}),
        }
    )
    assert fields.input == {"city": "Oslo"}
    assert fields.output == {"temp": 7}
    assert fields.kind is SpanKind.TOOL
    assert fields.consumed_keys == frozenset({"ai.toolCall.args", "ai.toolCall.result"})


def test_embed_values() -> None:
    fields = map_vercel_ai(
        {"ai.operationId": "ai.embedMany.doEmbed", "ai.values": json.dumps(["a", "b"])}
    )
    assert fields.input == ["a", "b"]
    assert fields.output is None
    single = map_vercel_ai({"ai.operationId": "ai.embed", "ai.value": json.dumps("a")})
    assert single.input == "a"


def test_empty_output_is_none() -> None:
    fields = map_vercel_ai({"ai.operationId": "ai.generateText", "ai.model.id": "gpt-4.1"})
    assert fields.output is None
    assert fields.input is None


@pytest.mark.parametrize(
    ("operation_id", "kind"),
    [
        ("ai.generateText", SpanKind.CHAIN),
        ("ai.streamText.doStream", SpanKind.LLM),
        ("ai.toolCall", SpanKind.TOOL),
        (None, SpanKind.OTHER),
    ],
)
def test_kind_from_operation_id(operation_id: str | None, kind: SpanKind) -> None:
    attributes = {"ai.model.id": "gpt-4.1"}
    if operation_id is not None:
        attributes["ai.operationId"] = operation_id
    fields = map_vercel_ai(attributes)
    assert fields.kind is kind
    assert fields.operation == operation_id


def test_metadata_prefixes_first_wins() -> None:
    fields = map_vercel_ai(
        {
            "ai.telemetry.metadata.Tenant": "acme",
            "ai.settings.context.tenant": "other",
            "ai.settings.context.region": "eu",
            "ai.telemetry.metadata.count": 3,
        }
    )
    assert fields.metadata == {"tenant": "acme", "region": "eu", "count": 3}
    assert fields.consumed_keys == frozenset(
        {
            "ai.telemetry.metadata.Tenant",
            "ai.settings.context.tenant",
            "ai.settings.context.region",
            "ai.telemetry.metadata.count",
        }
    )


def test_function_id_is_the_trace_name() -> None:
    assert map_vercel_ai({"ai.telemetry.functionId": "answer"}).trace_name == "answer"
    assert map_vercel_ai({}).trace_name is None


def test_all_content_keys_are_consumed_even_when_not_read() -> None:
    fields = map_vercel_ai(
        {
            "ai.prompt.messages": "[]",
            "ai.prompt": "{}",
            "ai.response.text": "",
            "ai.toolCall.result": "1",
        }
    )
    assert fields.input == []
    assert fields.output == 1
    assert fields.consumed_keys == frozenset(
        {"ai.prompt.messages", "ai.prompt", "ai.response.text", "ai.toolCall.result"}
    )
