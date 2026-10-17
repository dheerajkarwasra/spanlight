"""Unflattening indexed attributes (`llm.input_messages.0.message.role`) into messages (spec §4.3).

Indices are ASCII digits within four (leading zeros aside), in numeric order with gaps ignored;
wrapper segments are dropped; values stay as sent; keys past the limits are dropped and counted.
"""

from app.ingest.conventions.flatten import MAX_DEPTH, MAX_INDICES, Unflattened, unflatten

PREFIX = "llm.input_messages"


def test_messages_in_numeric_order_with_gaps() -> None:
    attributes = {
        f"{PREFIX}.3.message.role": "assistant",
        f"{PREFIX}.0.message.role": "system",
        f"{PREFIX}.10.message.role": "user",
        f"{PREFIX}.10.message.content": "hi",
    }
    result = unflatten(attributes, PREFIX)
    assert result.items == (
        {"role": "system"},
        {"role": "assistant"},
        {"role": "user", "content": "hi"},
    )
    assert result.consumed == frozenset(attributes)
    assert result.dropped == 0


def test_leading_zeros_are_an_index() -> None:
    result = unflatten({f"{PREFIX}.00012.message.role": "user"}, PREFIX)
    assert result.items == ({"role": "user"},)


def test_leading_zero_index_merges_with_plain_index() -> None:
    attributes = {f"{PREFIX}.1.message.role": "user", f"{PREFIX}.01.message.content": "hi"}
    result = unflatten(attributes, PREFIX)
    assert result.items == ({"role": "user", "content": "hi"},)
    assert result.consumed == frozenset(attributes)


def test_non_numeric_index_is_left_alone() -> None:
    result = unflatten({f"{PREFIX}.x.message.role": "user"}, PREFIX)
    assert result == Unflattened((), frozenset(), 0)


def test_five_digit_index_is_left_alone() -> None:
    result = unflatten({f"{PREFIX}.12345.message.role": "user"}, PREFIX)
    assert result == Unflattened((), frozenset(), 0)


def test_nested_contents_and_tool_calls() -> None:
    attributes = {
        f"{PREFIX}.0.message.role": "assistant",
        f"{PREFIX}.0.message.contents.1.message_content.text": "hello",
        f"{PREFIX}.0.message.tool_calls.0.tool_call.function.name": "lookup",
    }
    result = unflatten(attributes, PREFIX)
    assert result.items == (
        {
            "role": "assistant",
            "contents": [{"text": "hello"}],
            "tool_calls": [{"function": {"name": "lookup"}}],
        },
    )
    assert result.consumed == frozenset(attributes)


def test_gen_ai_prompt_has_no_wrapper_segment() -> None:
    result = unflatten({"gen_ai.prompt.0.role": "user"}, "gen_ai.prompt")
    assert result.items == ({"role": "user"},)


def test_over_256_indices_keeps_the_first_256_and_counts_the_rest() -> None:
    attributes = {f"{PREFIX}.{i}.message.role": "user" for i in range(300)}
    result = unflatten(attributes, PREFIX)
    assert len(result.items) == MAX_INDICES == 256
    assert result.dropped == 44
    # Dropped keys are still consumed: an unreadable fragment is not worth an attribute slot.
    assert result.consumed == frozenset(attributes)


def test_fifth_nesting_level_is_dropped_and_counted() -> None:
    assert MAX_DEPTH == 4
    four_deep = f"{PREFIX}.0.a.1.b.2.c.3.d"
    five_deep = f"{PREFIX}.0.a.1.b.2.c.3.d.4.e"
    result = unflatten({four_deep: "ok", five_deep: "too deep"}, PREFIX)
    assert result.items == ({"a": [{"b": [{"c": [{"d": "ok"}]}]}]},)
    assert result.dropped == 1
    assert five_deep in result.consumed


def test_content_json_text_is_not_parsed() -> None:
    content = '{"type": "text", "text": "hi"}'
    result = unflatten({f"{PREFIX}.0.message.content": content}, PREFIX)
    assert result.items == ({"content": content},)


def test_colliding_scalar_and_object_keeps_the_first_and_counts_the_other() -> None:
    attributes = {
        f"{PREFIX}.0.message.content": "plain",
        f"{PREFIX}.0.message.content.text": "nested",
    }
    result = unflatten(attributes, PREFIX)
    assert result.items == ({"content": "plain"},)
    assert result.dropped == 1


def test_no_matching_keys_is_empty() -> None:
    assert unflatten({"gen_ai.system": "openai"}, PREFIX) == Unflattened((), frozenset(), 0)
