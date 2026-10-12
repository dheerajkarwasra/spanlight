"""Propagation of the active span through :mod:`contextvars`.

Using a :class:`~contextvars.ContextVar` means the current span follows the
natural flow of the program: nested function calls see their caller's span,
and :mod:`asyncio` tasks inherit the span that was active when they were
created (each task runs in a copy of its creator's context). Threads do not
inherit context automatically; use :func:`contextvars.copy_context` and
``ctx.run(...)`` when handing work to a thread pool.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from contextvars import ContextVar, Token

from ._span import Span

_current_span: ContextVar[Span | None] = ContextVar("spanlight_current_span", default=None)
_current_prompt_version_id: ContextVar[str | None] = ContextVar(
    "spanlight_current_prompt_version_id", default=None
)


def get_current_span() -> Span | None:
    """Return the span active in the current context, if any."""
    return _current_span.get()


def get_current_trace_id() -> str | None:
    """Return the trace id of the active span, if any."""
    span = _current_span.get()
    return span.trace_id if span is not None else None


@contextlib.contextmanager
def activate(span: Span) -> Iterator[Span]:
    """Make ``span`` the current span for the duration of the ``with`` block."""
    token = _current_span.set(span)
    try:
        yield span
    finally:
        _current_span.reset(token)


def set_current_span(span: Span | None) -> Token[Span | None]:
    """Set the current span and return a token for :func:`restore_current_span`."""
    return _current_span.set(span)


def restore_current_span(token: Token[Span | None], previous: Span | None) -> None:
    """Undo :func:`set_current_span`.

    ``ContextVar.reset`` refuses tokens created in a different context (for
    example a ``with`` block entered in one task and exited in another). In
    that case the previous span is restored by value instead of raising.
    """
    try:
        _current_span.reset(token)
    except ValueError:
        _current_span.set(previous)


def get_current_prompt_version_id() -> str | None:
    """Return the prompt version id spans started now are linked to, if any."""
    return _current_prompt_version_id.get()


def set_current_prompt_version_id(version_id: str | None) -> Token[str | None]:
    """Link spans started from now on to ``version_id``; returns a token to undo it."""
    return _current_prompt_version_id.set(version_id)


def restore_current_prompt_version_id(token: Token[str | None], previous: str | None) -> None:
    """Undo :func:`set_current_prompt_version_id`, by value when the token is foreign."""
    try:
        _current_prompt_version_id.reset(token)
    except ValueError:
        _current_prompt_version_id.set(previous)
