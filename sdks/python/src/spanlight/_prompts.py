"""Prompts fetched from Spanlight, and their compiled form.

A :class:`Prompt` is one immutable prompt version (or a local fallback). Compiling it fills in
the variables and returns a :class:`CompiledPrompt`, which is also a context manager: spans
started inside ``with compiled:`` are linked to the prompt version they used.

The link lives in a :class:`~contextvars.ContextVar`, so it follows contextvars semantics: an
``asyncio`` task created inside the block keeps the link for its whole life, and a thread does
not see it at all. Wrap the creation of long-lived tasks in :func:`no_prompt` to keep their
spans unlinked.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator, Mapping, Sequence
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from types import TracebackType
from typing import Any

from ._context import (
    get_current_prompt_version_id,
    restore_current_prompt_version_id,
    set_current_prompt_version_id,
)
from ._templates import (
    Kind,
    Template,
    compile,
    extract_variables,
    messages_from_wire,
    template_from_wire,
)

# The `with compiled:` blocks open in the current context, innermost last: (prompt, token to
# undo its link, link before it). Kept per context so threads and tasks entering the same
# CompiledPrompt never pop each other's entries.
_Entered = tuple["CompiledPrompt", Token[str | None], str | None]
_entered: ContextVar[tuple[_Entered, ...]] = ContextVar("spanlight_entered_prompts", default=())


@contextlib.contextmanager
def no_prompt() -> Iterator[None]:
    """Unlink spans started inside the block from any prompt version.

    Use it around code that starts long-lived work (``asyncio.create_task``, a background
    worker) from inside ``with compiled:``, which would otherwise inherit the link.
    """
    previous = get_current_prompt_version_id()
    token = set_current_prompt_version_id(None)
    try:
        yield
    finally:
        restore_current_prompt_version_id(token, previous)


class PromptUnavailableError(Exception):
    """A prompt could not be fetched and there was neither a cached copy nor a fallback.

    This is the one exception the SDK raises on purpose. Pass ``fallback=`` to
    :meth:`PromptClient.get <spanlight.PromptClient.get>` to never see it.
    """


@dataclass(frozen=True)
class CompiledPrompt:
    """A prompt with its variables filled in.

    Use ``text`` for a text prompt and ``messages`` (plain ``{"role", "content"}`` dicts, ready
    for the OpenAI and Anthropic SDKs) for a chat prompt. Enter it with ``with compiled:`` to link
    every span started inside the block to this prompt version, or pass it to a wrapped client
    call as ``spanlight_prompt=compiled``. A fallback (no version) clears the link instead.

    Attributes:
        name: The prompt name.
        version: The prompt version, or ``None`` for a local fallback.
        prompt_version_id: The version id spans are linked to, or ``None`` for a fallback.
        kind: ``"text"`` or ``"chat"``.
        text: The compiled text of a text prompt.
        messages: The compiled messages of a chat prompt.
        variables_used: Variables the template uses, in first-appearance order.
        unused_variables: Variables that were passed but not used, sorted.
    """

    name: str
    version: int | None
    prompt_version_id: str | None
    kind: Kind
    text: str | None
    messages: list[dict[str, str]] | None
    variables_used: tuple[str, ...]
    unused_variables: tuple[str, ...]

    def __enter__(self) -> CompiledPrompt:
        previous = get_current_prompt_version_id()
        token = set_current_prompt_version_id(self.prompt_version_id)
        _entered.set((*_entered.get(), (self, token, previous)))
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        stack = _entered.get()
        for index in range(len(stack) - 1, -1, -1):
            prompt, token, previous = stack[index]
            if prompt is self:
                _entered.set(stack[:index] + stack[index + 1 :])
                restore_current_prompt_version_id(token, previous)
                return

    async def __aenter__(self) -> CompiledPrompt:
        return self.__enter__()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.__exit__(exc_type, exc, traceback)


@dataclass(frozen=True)
class Prompt:
    """One prompt version as served by Spanlight, or a local fallback.

    Attributes:
        name: The prompt name.
        version: The version number, or ``None`` when this is the fallback passed to ``get``.
        version_id: The version's id, or ``None`` for a fallback.
        kind: ``"text"`` or ``"chat"``.
        template: The uncompiled template.
        variables: Variables the template uses, in first-appearance order.
        model_config: Model settings saved with the version (model, temperature, ...).
        labels: Labels that point at this version.
    """

    name: str
    version: int | None
    version_id: str | None
    kind: Kind
    template: Template
    variables: tuple[str, ...]
    model_config: dict[str, Any] = field(default_factory=dict)
    labels: tuple[str, ...] = ()

    @property
    def is_fallback(self) -> bool:
        """Whether this prompt is the local fallback rather than a served version."""
        return self.version is None

    def compile(self, **variables: str | int | float) -> CompiledPrompt:
        """Fill in the template's variables.

        Values are strings; numbers and booleans are converted with ``str()``. Extra
        variables are reported in ``unused_variables``, never rejected.

        Raises:
            MissingVariablesError: A variable the template uses was not given.
            InvalidTemplateError: The template is malformed.
            TemplateTooLargeError: The rendered prompt would exceed 256 KB.
            TypeError: A value is not a string, number or boolean (``None`` included).
        """
        rendered = compile(self.template, _as_strings(variables))
        messages = (
            [{"role": message.role, "content": message.content} for message in rendered.messages]
            if rendered.messages is not None
            else None
        )
        return CompiledPrompt(
            name=self.name,
            version=self.version,
            prompt_version_id=self.version_id,
            kind=rendered.kind,
            text=rendered.text,
            messages=messages,
            variables_used=rendered.variables_used,
            unused_variables=rendered.unused_variables,
        )


def _as_strings(variables: Mapping[str, Any]) -> dict[str, str]:
    """Template values as strings: numbers and booleans via ``str()``, anything else refused."""
    converted: dict[str, str] = {}
    for name, value in variables.items():
        if isinstance(value, str):
            converted[name] = value
        elif isinstance(value, int | float):
            converted[name] = str(value)
        else:
            raise TypeError(
                f"variable {name!r} must be a string, number or boolean, not {type(value).__name__}"
            )
    return converted


def prompt_from_wire(body: Any) -> Prompt:
    """Build a :class:`Prompt` from a ``GET /v1/prompts/{name}`` response body.

    Raises:
        ValueError: The body does not have the documented shape.
    """
    if not isinstance(body, Mapping):
        raise ValueError("prompt response is not a JSON object")
    name, version, version_id = body.get("name"), body.get("version"), body.get("version_id")
    if not isinstance(name, str) or not isinstance(version_id, str):
        raise ValueError("prompt response lacks name or version_id")
    if not isinstance(version, int) or isinstance(version, bool):
        raise ValueError("prompt response lacks a version number")
    template = template_from_wire(body.get("template"))
    model_config = body.get("model_config")
    labels = body.get("labels")
    return Prompt(
        name=name,
        version=version,
        version_id=version_id,
        kind=template.kind,
        template=template,
        variables=extract_variables(template),
        model_config=dict(model_config) if isinstance(model_config, Mapping) else {},
        labels=tuple(str(label) for label in labels) if isinstance(labels, list) else (),
    )


def fallback_prompt(name: str, fallback: str | Sequence[Mapping[str, Any]]) -> Prompt:
    """Build the ``version=None`` prompt used when nothing could be fetched or cached.

    Raises:
        InvalidTemplateError: ``fallback`` is not a valid text or chat template.
    """
    template = (
        Template("text", text=fallback)
        if isinstance(fallback, str)
        else Template("chat", messages=messages_from_wire(list(fallback)))
    )
    return Prompt(
        name=name,
        version=None,
        version_id=None,
        kind=template.kind,
        template=template,
        variables=extract_variables(template),
    )
