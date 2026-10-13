"""Prompt template compiler: ``{{name}}`` substitution, pure and engine-free.

These are the same rules the Spanlight server and dashboard apply, checked against one shared
fixture file. A variable is ``{{name}}`` with optional spaces or tabs inside the braces; values
are inserted verbatim and never re-expanded. There are no sections, escapes or partials. The
rendered output is capped at 256 KB (UTF-8, the text or all messages together).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

MAX_TEMPLATE_BYTES = 64 * 1024
MAX_CHAT_MESSAGES = 50
# Bound on the rendered output (text, or the sum of every message), in UTF-8 bytes. Without it a
# small template that repeats a large variable could render hundreds of megabytes.
MAX_COMPILED_BYTES = 256 * 1024

Role = Literal["system", "user", "assistant"]
Kind = Literal["text", "chat"]

_ROLES: frozenset[str] = frozenset({"system", "user", "assistant"})
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
# A closed `{{ ... }}` on one line (inner text may be anything), or a lone `{{` that never closes.
_TOKEN = re.compile(r"\{\{(?P<inner>[^\n]*?)\}\}|\{\{")


class InvalidTemplateError(ValueError):
    """The template is malformed (bad braces, bad name, wrong shape or too large)."""


class MissingVariablesError(ValueError):
    """``compile`` was called without every variable the template uses.

    Attributes:
        names: Every missing variable name, sorted.
    """

    def __init__(self, names: tuple[str, ...]) -> None:
        super().__init__(f"missing variables: {', '.join(names)}")
        self.names = names


class TemplateTooLargeError(ValueError):
    """The rendered output would exceed ``MAX_COMPILED_BYTES`` (256 KB) with the variables in."""

    def __init__(self) -> None:
        super().__init__(f"compiled prompt exceeds {MAX_COMPILED_BYTES} bytes")


@dataclass(frozen=True)
class Message:
    """One message of a chat template."""

    role: Role
    content: str


@dataclass(frozen=True)
class Template:
    """A text template (``text``) or a chat template (``messages``)."""

    kind: Kind
    text: str | None = None
    messages: tuple[Message, ...] | None = None


@dataclass(frozen=True)
class RenderedTemplate:
    """The result of :func:`compile`."""

    kind: Kind
    text: str | None
    messages: tuple[Message, ...] | None
    variables_used: tuple[str, ...]
    unused_variables: tuple[str, ...]


# A scanned piece of a template: literal text, or a variable reference when `name` is set.
@dataclass(frozen=True)
class _Part:
    literal: str
    name: str | None = None


def _position(source: str, offset: int) -> str:
    line = source.count("\n", 0, offset) + 1
    column = offset - (source.rfind("\n", 0, offset) + 1) + 1
    return f"line {line}, column {column}"


def _scan(source: str) -> list[_Part]:
    parts: list[_Part] = []
    cursor = 0
    for match in _TOKEN.finditer(source):
        if match.start() > cursor:
            parts.append(_Part(source[cursor : match.start()]))
        inner = match.group("inner")
        if inner is None:
            raise InvalidTemplateError(f"unclosed '{{{{' at {_position(source, match.start())}")
        name = inner.strip(" \t")
        if not _NAME.fullmatch(name):
            raise InvalidTemplateError(
                f"invalid variable name at {_position(source, match.start())}"
            )
        parts.append(_Part("", name))
        cursor = match.end()
    if cursor < len(source):
        parts.append(_Part(source[cursor:]))
    return parts


def _sources(template: Template) -> list[str]:
    """Validate the template's shape and bounds, and return its text blocks in order."""
    if template.kind == "text":
        if template.text is None or template.messages is not None:
            raise InvalidTemplateError("a text template needs `text` and no `messages`")
        blocks = [template.text]
    elif template.kind == "chat":
        if template.messages is None or template.text is not None:
            raise InvalidTemplateError("a chat template needs `messages` and no `text`")
        if not 1 <= len(template.messages) <= MAX_CHAT_MESSAGES:
            raise InvalidTemplateError(f"a chat template needs 1 to {MAX_CHAT_MESSAGES} messages")
        blocks = [message.content for message in template.messages]
    else:
        raise InvalidTemplateError(f"unknown template kind: {template.kind!r}")
    try:
        size = sum(len(block.encode("utf-8")) for block in blocks)
    except UnicodeEncodeError:
        # A lone surrogate: not valid Unicode, and the server would refuse it as a template.
        raise InvalidTemplateError("template text is not valid Unicode") from None
    if size > MAX_TEMPLATE_BYTES:
        raise InvalidTemplateError(f"template exceeds {MAX_TEMPLATE_BYTES} bytes")
    return blocks


def _ordered_unique(parts_per_block: list[list[_Part]]) -> list[str]:
    seen: dict[str, None] = {}
    for parts in parts_per_block:
        for part in parts:
            if part.name is not None:
                seen.setdefault(part.name)
    return list(seen)


def extract_variables(template: Template) -> tuple[str, ...]:
    """Variable names in first-appearance order, de-duplicated."""
    return tuple(_ordered_unique([_scan(block) for block in _sources(template)]))


def _check_size(parts_per_block: list[list[_Part]], variables: Mapping[str, str]) -> None:
    """Raise `TemplateTooLargeError` before rendering when the output would pass the cap."""
    sizes: dict[str, int] = {}
    total = 0
    for parts in parts_per_block:
        for part in parts:
            if part.name is None:
                total += len(part.literal.encode("utf-8"))
            else:
                if part.name not in sizes:
                    # `surrogatepass`: a value may carry a lone surrogate (from `os.fsdecode`,
                    # say); count it rather than raise, since the value is the caller's data.
                    value = variables[part.name]
                    sizes[part.name] = len(value.encode("utf-8", "surrogatepass"))
                total += sizes[part.name]
            if total > MAX_COMPILED_BYTES:
                raise TemplateTooLargeError()


def _render(parts: list[_Part], variables: Mapping[str, str]) -> str:
    return "".join(
        variables[part.name] if part.name is not None else part.literal for part in parts
    )


def compile(template: Template, variables: Mapping[str, str]) -> RenderedTemplate:
    """Substitute ``variables`` into ``template``.

    Raises:
        InvalidTemplateError: The template is malformed.
        MissingVariablesError: A variable the template uses was not given.
        TemplateTooLargeError: The rendered output would exceed 256 KB (UTF-8, the text or all
            messages together).
    """
    scanned = [_scan(block) for block in _sources(template)]
    used = _ordered_unique(scanned)
    missing = tuple(sorted(name for name in used if name not in variables))
    if missing:
        raise MissingVariablesError(missing)
    unused = tuple(sorted(name for name in variables if name not in used))
    _check_size(scanned, variables)
    if template.kind == "text":
        return RenderedTemplate("text", _render(scanned[0], variables), None, tuple(used), unused)
    assert template.messages is not None  # narrowed by _sources
    messages = tuple(
        Message(message.role, _render(parts, variables))
        for message, parts in zip(template.messages, scanned, strict=True)
    )
    return RenderedTemplate("chat", None, messages, tuple(used), unused)


def template_from_wire(raw: Any) -> Template:
    """Build a :class:`Template` from its JSON form.

    The JSON form is ``{"kind": "text", "text": ...}`` or
    ``{"kind": "chat", "messages": [{"role": ..., "content": ...}, ...]}``.

    Raises:
        InvalidTemplateError: ``raw`` does not have that shape.
    """
    if not isinstance(raw, Mapping):
        raise InvalidTemplateError("a template must be a JSON object")
    kind = raw.get("kind")
    if kind == "text":
        text = raw.get("text")
        if not isinstance(text, str) or raw.get("messages") is not None:
            raise InvalidTemplateError("a text template needs a string `text` and no `messages`")
        return Template("text", text=text)
    if kind == "chat":
        if raw.get("text") is not None:
            raise InvalidTemplateError("a chat template needs `messages` and no `text`")
        return Template("chat", messages=messages_from_wire(raw.get("messages")))
    raise InvalidTemplateError(f"unknown template kind: {kind!r}")


def messages_from_wire(raw: Any) -> tuple[Message, ...]:
    """Build chat messages from a list of ``{"role", "content"}`` objects.

    Raises:
        InvalidTemplateError: An item is not a message with a known role and string content.
    """
    if not isinstance(raw, list | tuple):
        raise InvalidTemplateError("a chat template needs a list of `messages`")
    messages: list[Message] = []
    for index, item in enumerate(raw):
        role = item.get("role") if isinstance(item, Mapping) else None
        content = item.get("content") if isinstance(item, Mapping) else None
        if role not in _ROLES or not isinstance(content, str):
            raise InvalidTemplateError(
                f"message {index} needs a role of system, user or assistant and string content"
            )
        messages.append(Message(role, content))
    return tuple(messages)
