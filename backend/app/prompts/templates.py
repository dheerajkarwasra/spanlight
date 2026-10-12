"""Prompt template compiler: ``{{name}}`` substitution, pure and engine-free.

Values are inserted verbatim and never re-expanded. There are no sections, escapes or partials.
The rendered output is capped at 256 KB (UTF-8, text or all messages together).
The same rules are implemented by the Python SDK and the dashboard, which share one fixture file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

MAX_TEMPLATE_BYTES = 64 * 1024
MAX_CHAT_MESSAGES = 50
# Bound on the rendered output (text, or the sum of every message), in UTF-8 bytes. Without it a
# small template that repeats a large variable could render hundreds of megabytes.
MAX_COMPILED_BYTES = 256 * 1024

Role = Literal["system", "user", "assistant"]
Kind = Literal["text", "chat"]

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
# A closed `{{ ... }}` on one line (inner text may be anything), or a lone `{{` that never closes.
_TOKEN = re.compile(r"\{\{(?P<inner>[^\n]*?)\}\}|\{\{")


class InvalidTemplateError(ValueError):
    """The template is malformed (bad braces, bad name, wrong shape or too large)."""


class MissingVariablesError(ValueError):
    """Compile was called without every variable the template uses."""

    def __init__(self, names: tuple[str, ...]) -> None:
        super().__init__(f"missing variables: {', '.join(names)}")
        self.names = names


class TemplateTooLargeError(ValueError):
    """The rendered output would exceed `MAX_COMPILED_BYTES` (256 KB) with the variables in."""

    def __init__(self) -> None:
        super().__init__(f"compiled prompt exceeds {MAX_COMPILED_BYTES} bytes")


@dataclass(frozen=True, slots=True)
class Message:
    role: Role
    content: str


@dataclass(frozen=True, slots=True)
class Template:
    kind: Kind
    text: str | None = None
    messages: list[Message] | None = None


@dataclass(frozen=True, slots=True)
class CompiledPrompt:
    kind: Kind
    text: str | None
    messages: list[Message] | None
    variables_used: tuple[str, ...]
    unused_variables: tuple[str, ...]


# A scanned piece of a template: literal text, or a variable reference when `name` is set.
@dataclass(frozen=True, slots=True)
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
    if sum(len(block.encode("utf-8")) for block in blocks) > MAX_TEMPLATE_BYTES:
        raise InvalidTemplateError(f"template exceeds {MAX_TEMPLATE_BYTES} bytes")
    return blocks


def _ordered_unique(parts_per_block: list[list[_Part]]) -> list[str]:
    seen: dict[str, None] = {}
    for parts in parts_per_block:
        for part in parts:
            if part.name is not None:
                seen.setdefault(part.name)
    return list(seen)


def extract_variables(template: Template) -> list[str]:
    """Variable names in first-appearance order, de-duplicated."""
    return _ordered_unique([_scan(block) for block in _sources(template)])


def _check_size(parts_per_block: list[list[_Part]], variables: dict[str, str]) -> None:
    """Raise `TemplateTooLargeError` before rendering when the output would pass the cap."""
    sizes: dict[str, int] = {}
    total = 0
    for parts in parts_per_block:
        for part in parts:
            if part.name is None:
                total += len(part.literal.encode("utf-8"))
            else:
                if part.name not in sizes:
                    sizes[part.name] = len(variables[part.name].encode("utf-8"))
                total += sizes[part.name]
            if total > MAX_COMPILED_BYTES:
                raise TemplateTooLargeError()


def _render(parts: list[_Part], variables: dict[str, str]) -> str:
    return "".join(
        variables[part.name] if part.name is not None else part.literal for part in parts
    )


def compile(template: Template, variables: dict[str, str]) -> CompiledPrompt:
    """Substitute `variables` into the template. Extra variables are reported, not rejected.

    Raises `InvalidTemplateError`, `MissingVariablesError`, or `TemplateTooLargeError` when the
    rendered output would exceed `MAX_COMPILED_BYTES` (256 KB, text or all messages together).
    """
    scanned = [_scan(block) for block in _sources(template)]
    used = _ordered_unique(scanned)
    missing = tuple(sorted(name for name in used if name not in variables))
    if missing:
        raise MissingVariablesError(missing)
    unused = tuple(sorted(name for name in variables if name not in used))
    _check_size(scanned, variables)
    if template.kind == "text":
        return CompiledPrompt("text", _render(scanned[0], variables), None, tuple(used), unused)
    assert template.messages is not None  # noqa: S101 - narrowed by _sources
    messages = [
        Message(message.role, _render(parts, variables))
        for message, parts in zip(template.messages, scanned, strict=True)
    ]
    return CompiledPrompt("chat", None, messages, tuple(used), unused)
