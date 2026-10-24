"""One short readable line from the start of a payload's JSON text, for the trace list. Pure.

The text is at most the first `PREVIEW_SOURCE_CHARS` characters of the stored JSON
(`app.explorer.trace_previews`), so it is often cut. Complete JSON is read for the text a person
would recognise: the last user message of a chat input, the assistant text of an OpenAI or
Anthropic output, a named text field, else compact `key: value` pairs. A cut prefix is shown
with its JSON punctuation removed. Base64-looking runs become `[binary]`, so an inline image
never reaches the list.
"""

import json
import re
from typing import Any, Literal

PREVIEW_SOURCE_CHARS = 4000
PREVIEW_MAX_CHARS = 200

# Fields whose string value is the payload's text, in order of preference.
NAMED_TEXT_FIELDS = ("text", "content", "output", "answer", "report", "query", "prompt", "input")

_BASE64_RUN = re.compile(r"[A-Za-z0-9+/=]{64,}")
_JSON_ESCAPE = re.compile(r'\\[nrt"\\/]')
_JSON_PUNCTUATION = re.compile(r'[{}\[\]"\\]')


def preview_text(raw: str | None, *, role: Literal["input", "output"]) -> str | None:
    """At most `PREVIEW_MAX_CHARS` of readable text from `raw`; None when nothing is readable."""
    if raw is None:
        return None
    try:
        value = json.loads(raw)
    except ValueError:
        text: str | None = _flatten(raw)
    else:
        text = _readable(value, role)
    if not text:
        return None
    text = " ".join(_BASE64_RUN.sub("[binary]", text).split())
    return _cut(text) if text else None


def _readable(value: Any, role: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        messages = [item for item in value if isinstance(item, dict)]
        return _message_text(messages, role) if messages else _flatten(json.dumps(value))
    if not isinstance(value, dict):
        return json.dumps(value)
    if isinstance(value.get("messages"), list):
        return _readable(value["messages"], role)
    choices = value.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        return _readable(choices[0].get("message") or choices[0], role)
    for field in NAMED_TEXT_FIELDS:
        if field in value and (text := _content_text(value[field])) is not None:
            return text
    return ", ".join(f"{key}: {_scalar(item)}" for key, item in value.items())


def _message_text(messages: list[dict[str, Any]], role: str) -> str | None:
    """The last message of the side's role (user for input, assistant for output), else the last.

    An item that is not a chat message (no `role`, as a list of documents) or whose content has
    no text (an image only) is read as any other object.
    """
    wanted = "user" if role == "input" else "assistant"
    picked = next((m for m in reversed(messages) if m.get("role") == wanted), messages[-1])
    text = _content_text(picked.get("content")) if "role" in picked else None
    return text if text is not None else _readable(picked, role)


def _content_text(content: Any) -> str | None:
    """A string, or the text blocks of a content list joined; None for anything else."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = [block.get("text") for block in content if isinstance(block, dict)]
        return " ".join(t for t in texts if isinstance(t, str)) or None
    return None


def _scalar(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, separators=(",", ":"))


def _flatten(raw: str) -> str:
    return _JSON_PUNCTUATION.sub(" ", _JSON_ESCAPE.sub(" ", raw))


def _cut(text: str) -> str:
    """`text`, or its start cut at a word boundary with "…", `PREVIEW_MAX_CHARS` at most."""
    if len(text) <= PREVIEW_MAX_CHARS:
        return text
    head = text[: PREVIEW_MAX_CHARS - 1]
    space = head.rfind(" ")
    # A long unbroken token (a URL) is cut mid-word rather than leaving a few characters.
    if text[len(head)] != " " and space > PREVIEW_MAX_CHARS // 2:
        head = head[:space]
    return head.rstrip() + "…"
