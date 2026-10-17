"""The OpenInference mapper (spec §4.3 `openinference` table, §12 rows).

One test per field source, plus the §12 rows that belong to the mapper: messages beat
`input.value`, `input.value` is parsed only under a JSON mime type, an unknown or non-string
span kind is `other` with the operation kept, `metadata` that is not a JSON object is ignored.
"""

from app.db.models.telemetry import SpanKind
from app.ingest.conventions.openinference import map_openinference
from app.ingest.conventions.types import MappedUsage


def test_llm_system_beats_llm_provider() -> None:
    fields = map_openinference({"llm.system": "openai", "llm.provider": "azure"})
    assert fields.provider == "openai"
    assert map_openinference({"llm.provider": "azure"}).provider == "azure"


def test_model_name_precedence() -> None:
    attributes = {
        "embedding.model_name": "text-embedding-3-small",
        "llm.request.model_name": "gpt-4o",
        "llm.response.model_name": "gpt-4o-2024-08-06",
        "llm.model_name": "gpt-4o-mini",
    }
    assert map_openinference(attributes).model == "gpt-4o-mini"
    del attributes["llm.model_name"]
    assert map_openinference(attributes).model == "gpt-4o-2024-08-06"
    del attributes["llm.response.model_name"]
    assert map_openinference(attributes).model == "gpt-4o"
    del attributes["llm.request.model_name"]
    assert map_openinference(attributes).model == "text-embedding-3-small"


def test_token_counts_and_cache_details() -> None:
    fields = map_openinference(
        {
            "llm.token_count.prompt": 120,
            "llm.token_count.completion": 30,
            "llm.token_count.prompt_details.cache_read": 100,
            "llm.token_count.prompt_details.cache_write": 20,
        }
    )
    assert fields.usage == MappedUsage(
        input_tokens=120, output_tokens=30, cached_tokens=100, cache_write_tokens=20
    )


def test_total_and_reasoning_counts_are_left_in_attributes() -> None:
    attributes = {
        "llm.token_count.prompt": 120,
        "llm.token_count.completion": 30,
        "llm.token_count.total": 150,
        "llm.token_count.completion_details.reasoning": 10,
        "llm.token_count.prompt_details.audio": 5,
    }
    fields = map_openinference(attributes)
    assert fields.usage == MappedUsage(input_tokens=120, output_tokens=30)
    assert not fields.consumed_keys & {
        "llm.token_count.total",
        "llm.token_count.completion_details.reasoning",
        "llm.token_count.prompt_details.audio",
    }


def test_flattened_messages_win_over_input_value() -> None:
    attributes = {
        "llm.input_messages.0.message.role": "user",
        "llm.input_messages.0.message.content": "hi",
        "input.value": '{"messages": []}',
        "input.mime_type": "application/json",
        "llm.output_messages.0.message.role": "assistant",
        "llm.output_messages.0.message.content": "hello",
        "output.value": "hello",
    }
    fields = map_openinference(attributes)
    assert fields.input == [{"role": "user", "content": "hi"}]
    assert fields.output == [{"role": "assistant", "content": "hello"}]
    assert fields.consumed_keys == frozenset(attributes) - {"input.mime_type"}


def test_input_value_json_only_with_json_mime() -> None:
    fields = map_openinference(
        {
            "input.value": '{"messages": [{"role": "user", "content": "hi"}]}',
            "input.mime_type": "application/json",
            "output.value": '{"choices": []}',
            "output.mime_type": "application/json",
        }
    )
    assert fields.input == {"messages": [{"role": "user", "content": "hi"}]}
    assert fields.output == {"choices": []}
    assert fields.consumed_keys == frozenset({"input.value", "output.value"})


def test_input_value_text_kept_as_text() -> None:
    fields = map_openinference({"input.value": '{"a": 1}', "input.mime_type": "text/plain"})
    assert fields.input == '{"a": 1}'
    assert map_openinference({"input.value": '{"a": 1}'}).input == '{"a": 1}'


def test_kind_and_operation_from_span_kind() -> None:
    fields = map_openinference({"openinference.span.kind": "RETRIEVER"})
    assert fields.kind == SpanKind.RETRIEVAL
    assert fields.operation == "retriever"


def test_non_string_span_kind_is_other() -> None:
    fields = map_openinference({"openinference.span.kind": 7})
    assert fields.kind == SpanKind.OTHER
    assert fields.operation == "7"


def test_exception_message_is_the_status_fallback() -> None:
    fields = map_openinference({"exception.message": "boom", "llm.finish_reason": "stop"})
    assert fields.status_message_fallback == "boom"
    assert fields.finish_reason_raw == "stop"


def test_metadata_object_values_are_passed_raw() -> None:
    fields = map_openinference({"metadata": '{"tenant": "acme", "n": 3, "tags": ["a"]}'})
    assert fields.metadata == {"tenant": "acme", "n": 3, "tags": ["a"]}
    assert fields.consumed_keys == frozenset({"metadata"})


def test_metadata_not_json_is_ignored() -> None:
    for value in ("not json", "[1, 2]", 7):
        fields = map_openinference({"metadata": value})
        assert fields.metadata == {}
        assert fields.consumed_keys == frozenset()
