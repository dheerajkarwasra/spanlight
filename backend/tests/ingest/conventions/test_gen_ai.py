"""The `gen_ai` mapper (spec §4.3 `gen_ai` table, §4.6, §4.7, §12 rows).

One test per field source, plus the rows that belong to the mapper: flattened `gen_ai.prompt.N.*`
keys are read only when the JSON content keys are absent, `gen_ai.system_instructions` completes a
list input, TTFT seconds become milliseconds, the flattened finish reason is passed raw before
unflattening consumes it, and `consumed_keys` holds only what was read.
"""

import pytest

from app.db.models.telemetry import SpanKind
from app.ingest.conventions.gen_ai import map_gen_ai
from app.ingest.conventions.types import MappedUsage


@pytest.mark.parametrize(
    ("attributes", "field_name", "expected"),
    [
        ({"gen_ai.provider.name": "openai"}, "provider", "openai"),
        ({"gen_ai.system": "anthropic"}, "provider", "anthropic"),
        ({"gen_ai.response.model": "gpt-4.1-2025-04-14"}, "model", "gpt-4.1-2025-04-14"),
        ({"gen_ai.request.model": "gpt-4.1"}, "model", "gpt-4.1"),
        ({"gen_ai.input.messages": '[{"role": "user"}]'}, "input", [{"role": "user"}]),
        ({"gen_ai.prompt": "plain prompt"}, "input", "plain prompt"),
        ({"gen_ai.output.messages": '[{"role": "assistant"}]'}, "output", [{"role": "assistant"}]),
        ({"gen_ai.completion": '{"text": "hi"}'}, "output", {"text": "hi"}),
        ({"gen_ai.operation.name": "chat"}, "operation", "chat"),
        ({"gen_ai.client.operation.time_to_first_chunk": 1}, "time_to_first_token_ms", 1000),
        ({"gen_ai.response.time_to_first_chunk": 0.5}, "time_to_first_token_ms", 500.0),
        ({"gen_ai.completion.0.finish_reason": "stop"}, "finish_reason_raw", "stop"),
    ],
)
def test_field_sources(attributes: dict[str, object], field_name: str, expected: object) -> None:
    assert getattr(map_gen_ai(attributes), field_name) == expected


def test_provider_name_beats_system() -> None:
    fields = map_gen_ai({"gen_ai.system": "openai", "gen_ai.provider.name": "azure"})
    assert fields.provider == "azure"


def test_response_model_beats_request_model() -> None:
    fields = map_gen_ai(
        {"gen_ai.request.model": "gpt-4.1", "gen_ai.response.model": "gpt-4.1-2025-04-14"}
    )
    assert fields.model == "gpt-4.1-2025-04-14"


def test_usage_new_and_old_names() -> None:
    new = map_gen_ai(
        {
            "gen_ai.usage.input_tokens": 120,
            "gen_ai.usage.output_tokens": 30,
            "gen_ai.usage.cache_read.input_tokens": 100,
            "gen_ai.usage.cache_creation.input_tokens": 20,
        }
    )
    old = map_gen_ai(
        {
            "gen_ai.usage.prompt_tokens": 120,
            "gen_ai.usage.completion_tokens": 30,
            "gen_ai.usage.cache_read_input_tokens": 100,
            "gen_ai.usage.cache_creation_input_tokens": 20,
        }
    )
    expected = MappedUsage(
        input_tokens=120, output_tokens=30, cached_tokens=100, cache_write_tokens=20
    )
    assert new.usage == expected
    assert old.usage == expected
    assert map_gen_ai({"gen_ai.system": "openai"}).usage == MappedUsage()


def test_service_tier_and_web_search_attributes_carried() -> None:
    attributes = {
        "gen_ai.system": "openai",
        "openai.response.service_tier": "flex",
        "gen_ai.openai.response.service_tier": "priority",
        "spanlight.usage.service_tier": "batch",
        "spanlight.usage.web_search_requests": 2,
    }
    fields = map_gen_ai(attributes)
    assert fields.usage.service_tier == "flex"
    assert fields.usage.service_tier_raw is None
    assert fields.usage.web_search_requests == 2
    del attributes["openai.response.service_tier"]
    assert map_gen_ai(attributes).usage.service_tier == "priority"
    del attributes["gen_ai.openai.response.service_tier"]
    assert map_gen_ai(attributes).usage.service_tier == "batch"
    unknown = map_gen_ai({"gen_ai.system": "openai", "openai.response.service_tier": " turbo "})
    assert unknown.usage.service_tier is None
    assert unknown.usage.service_tier_raw == "turbo"


def test_flattened_prompt_and_completion_are_read_when_json_keys_absent() -> None:
    attributes = {
        "gen_ai.prompt.0.role": "system",
        "gen_ai.prompt.0.content": "be brief",
        "gen_ai.prompt.1.role": "user",
        "gen_ai.prompt.1.content": "hi",
        "gen_ai.completion.0.role": "assistant",
        "gen_ai.completion.0.content": "hello",
        "gen_ai.completion.0.finish_reason": "stop",
    }
    fields = map_gen_ai(attributes)
    assert fields.input == [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "hi"},
    ]
    assert fields.output == [{"role": "assistant", "content": "hello", "finish_reason": "stop"}]
    assert fields.consumed_keys == frozenset(attributes)
    assert fields.dropped_messages == 0

    json_wins = map_gen_ai({**attributes, "gen_ai.prompt": '[{"role": "user", "content": "x"}]'})
    assert json_wins.input == [{"role": "user", "content": "x"}]
    assert "gen_ai.prompt.0.role" not in json_wins.consumed_keys


def test_flattened_messages_past_the_limit_are_dropped_and_counted() -> None:
    attributes = {f"gen_ai.prompt.{index}.role": "user" for index in range(300)}
    fields = map_gen_ai(attributes)
    assert len(fields.input) == 256
    assert fields.dropped_messages == 44


def test_system_instructions_prepended_to_a_list_input() -> None:
    fields = map_gen_ai(
        {
            "gen_ai.system_instructions": '[{"type": "text", "content": "be brief"}]',
            "gen_ai.input.messages": '[{"role": "user", "parts": [{"content": "hi"}]}]',
        }
    )
    assert fields.input == [
        {"role": "system", "parts": [{"type": "text", "content": "be brief"}]},
        {"role": "user", "parts": [{"content": "hi"}]},
    ]
    assert "gen_ai.system_instructions" in fields.consumed_keys


def test_system_instructions_not_prepended_to_text_input() -> None:
    fields = map_gen_ai({"gen_ai.system_instructions": "be brief", "gen_ai.prompt": "hi"})
    assert fields.input == "hi"
    # Prompt text, so it is consumed even when it is not prepended (ruling C-1).
    assert "gen_ai.system_instructions" in fields.consumed_keys
    assert map_gen_ai({"gen_ai.system_instructions": "be brief"}).input is None


def test_operation_sets_kind_and_operation() -> None:
    tool = map_gen_ai({"gen_ai.operation.name": "execute_tool"})
    assert tool.kind is SpanKind.TOOL
    assert tool.operation == "execute_tool"
    agent = map_gen_ai({"gen_ai.operation.name": " Invoke_Agent "})
    assert agent.kind is SpanKind.AGENT
    assert agent.operation == " Invoke_Agent "
    unknown = map_gen_ai({"gen_ai.operation.name": "mystery"})
    assert unknown.kind is SpanKind.OTHER
    assert unknown.operation == "mystery"


def test_no_operation_is_llm() -> None:
    fields = map_gen_ai({"gen_ai.request.model": "gpt-4.1"})
    assert fields.kind is SpanKind.LLM
    assert fields.operation is None


def test_ttft_seconds_to_ms() -> None:
    assert (
        map_gen_ai({"gen_ai.client.operation.time_to_first_chunk": 0.42}).time_to_first_token_ms
        == 420.0
    )
    both = map_gen_ai(
        {
            "gen_ai.client.operation.time_to_first_chunk": 0.42,
            "gen_ai.response.time_to_first_chunk": 9,
        }
    )
    assert both.time_to_first_token_ms == 420.0
    # Numeric text is seconds too (Review Focus 2); junk is left for the native fit to drop.
    text = map_gen_ai({"gen_ai.client.operation.time_to_first_chunk": "0.42"})
    assert text.time_to_first_token_ms == 420.0
    junk = map_gen_ai({"gen_ai.client.operation.time_to_first_chunk": "soon"})
    assert junk.time_to_first_token_ms == "soon"
    assert map_gen_ai({}).time_to_first_token_ms is None


def test_completion_finish_reason_is_passed_raw() -> None:
    fields = map_gen_ai({"gen_ai.completion.0.finish_reason": "MAX_TOKENS"})
    assert fields.finish_reason_raw == "MAX_TOKENS"
    assert map_gen_ai({"gen_ai.completion": "[]"}).finish_reason_raw is None


def test_all_content_keys_consumed_even_when_not_read() -> None:
    attributes = {
        "gen_ai.system": "openai",
        "gen_ai.request.model": "gpt-4.1",
        "gen_ai.usage.input_tokens": 1,
        "gen_ai.input.messages": "[]",
        "gen_ai.prompt": "loses to the messages key, still prompt text",
        "gen_ai.output.messages": "[]",
        "spanlight.metadata.Tenant-ID": "acme",
        "custom.key": "kept",
    }
    fields = map_gen_ai(attributes)
    assert fields.input == []
    # The loser is consumed too (ruling C-1): content is stored under the payload rules only.
    assert fields.consumed_keys == frozenset(
        {"gen_ai.input.messages", "gen_ai.prompt", "gen_ai.output.messages"}
    )
    assert "custom.key" not in fields.consumed_keys
    assert "spanlight.metadata.Tenant-ID" not in fields.consumed_keys
    # Metadata has one owner (`otlp_metadata.trace_metadata`); the mapper leaves it alone.
    assert fields.metadata == {}
