"""`preview_text`: a payload's JSON text prefix reduced to one short readable line."""

import json

from app.explorer.preview_text import preview_text


def test_chat_input_uses_last_user_message() -> None:
    raw = json.dumps(
        {
            "messages": [
                {"role": "system", "content": "You are terse."},
                {"role": "user", "content": "first question"},
                {"role": "assistant", "content": "first answer"},
                {"role": "user", "content": [{"type": "text", "text": "second   question"}]},
            ]
        }
    )
    assert preview_text(raw, role="input") == "second question"


def test_list_of_documents_reads_as_objects() -> None:
    docs = [{"id": 1, "text": "Shipping times"}, {"id": 2, "text": "Refund policy"}]
    assert preview_text(json.dumps(docs), role="output") == "Refund policy"
    pages = json.dumps([{"page_content": "Returns within 30 days", "score": 0.9}])
    assert preview_text(pages, role="input") == "page_content: Returns within 30 days, score: 0.9"


def test_image_only_user_message_is_not_empty() -> None:
    image = {"type": "image_url", "image_url": {"url": "https://example.com/a.png"}}
    raw = json.dumps({"messages": [{"role": "user", "content": [image]}]})
    preview = preview_text(raw, role="input")
    assert preview is not None and "a.png" in preview


def test_openai_output_uses_assistant_content() -> None:
    raw = json.dumps(
        {"id": "x", "choices": [{"message": {"role": "assistant", "content": "the answer"}}]}
    )
    assert preview_text(raw, role="output") == "the answer"


def test_anthropic_output_joins_text_blocks() -> None:
    raw = json.dumps(
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "part one"},
                {"type": "tool_use", "name": "search", "input": {}},
                {"type": "text", "text": "part two"},
            ],
        }
    )
    assert preview_text(raw, role="output") == "part one part two"


def test_named_string_field_wins() -> None:
    raw = json.dumps({"draft": 3, "report": "Quarterly spend is flat."})
    assert preview_text(raw, role="output") == "Quarterly spend is flat."
    assert preview_text(json.dumps({"a": 1, "b": "two"}), role="input") == "a: 1, b: two"
    assert preview_text(json.dumps("just text"), role="input") == "just text"


def test_cut_json_prefix_is_flattened() -> None:
    raw = '{"messages": [{"role": "user", "content": "hello\\nthere'
    assert preview_text(raw, role="input") == "messages : role : user , content : hello there"
    # A truncated payload is stored as a JSON string of the original's start.
    stored = json.dumps('{"query": "what is the weather"}')[:22]
    assert preview_text(stored, role="input") == "query : what is"


def test_preview_text_replaces_base64_runs() -> None:
    image = "iVBORw0KGgo" + "A" * 5000
    raw = json.dumps({"text": f"look at data:image/png;base64,{image} please"})
    assert preview_text(raw, role="input") == "look at data:image/png;base64,[binary] please"
    assert preview_text(json.dumps({"text": image})[:4000], role="input") == "text : [binary]"


def test_preview_is_at_most_200_chars_on_a_word_boundary() -> None:
    words = " ".join(f"word{index}" for index in range(100))
    preview = preview_text(json.dumps(words), role="input")
    assert preview is not None
    assert len(preview) <= 200
    assert preview.endswith("…")
    assert words.startswith(preview[:-1])
    assert words[len(preview) - 1] == " "


def test_empty_or_null_is_none() -> None:
    for raw in (None, "", "null", '""', "   ", "{}", "[]", '{"text": "  "}'):
        assert preview_text(raw, role="input") is None, raw
