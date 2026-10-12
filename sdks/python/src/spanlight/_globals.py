"""Process-wide default client and the module-level convenience API."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Mapping
from typing import Any, ParamSpec, TypeVar, overload

import httpx

from ._client import SpanContext, Spanlight
from ._decorator import build_observe
from ._prompt_client import Fallback
from ._prompts import Prompt
from ._scores import ScoreValue
from ._span import SpanKind

logger = logging.getLogger("spanlight")

P = ParamSpec("P")
R = TypeVar("R")

_client: Spanlight | None = None
_client_lock = threading.Lock()


def init(
    *,
    api_key: str | None = None,
    host: str | None = None,
    environment: str | None = None,
    release: str | None = None,
    enabled: bool | None = None,
    flush_interval: float = 2.0,
    batch_size: int = 100,
    max_queue: int = 10_000,
    gzip: bool = False,
    timeout: float = 10.0,
    max_retries: int = 5,
    shutdown_timeout: float = 5.0,
    prompt_cache_ttl: float = 60.0,
    transport: httpx.BaseTransport | None = None,
) -> Spanlight:
    """Configure the global client used by :func:`observe`, :func:`span` and the wrappers.

    Accepts the same arguments as :class:`Spanlight`. Calling ``init`` again
    replaces the global client; the previous one is flushed and shut down.

    Returns:
        The new global :class:`Spanlight`.
    """
    global _client
    client = Spanlight(
        api_key=api_key,
        host=host,
        environment=environment,
        release=release,
        enabled=enabled,
        flush_interval=flush_interval,
        batch_size=batch_size,
        max_queue=max_queue,
        gzip=gzip,
        timeout=timeout,
        max_retries=max_retries,
        shutdown_timeout=shutdown_timeout,
        prompt_cache_ttl=prompt_cache_ttl,
        transport=transport,
    )
    with _client_lock:
        previous, _client = _client, client
    if previous is not None:
        previous.shutdown()
    return client


def get_client() -> Spanlight:
    """Return the global client, creating one from ``SPANLIGHT_*`` variables if needed."""
    global _client
    with _client_lock:
        if _client is None:
            _client = Spanlight()
        return _client


def span(
    name: str,
    *,
    kind: SpanKind = "chain",
    input: Any = None,
    attributes: Mapping[str, Any] | None = None,
) -> SpanContext:
    """Trace a block of code with the global client. See :meth:`Spanlight.span`."""
    return get_client().span(name, kind=kind, input=input, attributes=attributes)


@overload
def observe(func: Callable[P, R], /) -> Callable[P, R]: ...


@overload
def observe(
    name: str | None = None,
    *,
    kind: SpanKind = "chain",
    capture_input: bool = True,
    capture_output: bool = True,
) -> Callable[[Callable[P, R]], Callable[P, R]]: ...


def observe(
    name: Callable[P, R] | str | None = None,
    *,
    kind: SpanKind = "chain",
    capture_input: bool = True,
    capture_output: bool = True,
) -> Callable[P, R] | Callable[[Callable[P, R]], Callable[P, R]]:
    """Trace every call of the decorated function as a span.

    Works on plain functions, ``async def`` coroutines, generators and async
    generators. For generators the span stays open until iteration finishes.
    The global client is looked up at call time, so the decorator can be
    applied at import time, before :func:`init` runs.

    Example:
        >>> @observe  # doctest: +SKIP
        ... def answer(question: str) -> str: ...
        >>> @observe(name="retrieve", kind="retrieval")  # doctest: +SKIP
        ... async def search(query: str) -> list[str]: ...

    Args:
        name: Span name; defaults to the function's ``__name__``. (In the bare
            ``@observe`` form this parameter receives the function itself.)
        kind: Span kind: ``llm``, ``tool``, ``retrieval``, ``chain``,
            ``http`` or ``other``.
        capture_input: Record the call arguments as the span input.
        capture_output: Record the return value (or yielded items) as output.
    """
    return build_observe(
        get_client,
        name,
        kind=kind,
        capture_input=capture_input,
        capture_output=capture_output,
    )


class _GlobalPrompts:
    """``spanlight.prompts``: the prompt client of whichever global client is current."""

    def get(
        self,
        name: str,
        label: str = "production",
        *,
        version: int | None = None,
        fallback: Fallback | None = None,
    ) -> Prompt:
        """Fetch a prompt with the global client. See :meth:`PromptClient.get`."""
        return get_client().prompts.get(name, label, version=version, fallback=fallback)

    def __repr__(self) -> str:
        return "spanlight.prompts"


prompts = _GlobalPrompts()
"""Fetch prompts with the global client: ``spanlight.prompts.get("support/answer")``."""


def score(
    trace_id: str,
    name: str,
    value: ScoreValue,
    *,
    span_id: str | None = None,
    comment: str | None = None,
) -> None:
    """Record a score with the global client. See :meth:`Spanlight.score`."""
    try:
        get_client().score(trace_id, name, value, span_id=span_id, comment=comment)
    except Exception:
        logger.debug("score() failed", exc_info=True)


def flush(timeout: float | None = None) -> bool:
    """Flush the global client. See :meth:`Spanlight.flush`."""
    with _client_lock:
        client = _client
    return client.flush(timeout) if client is not None else True


def shutdown(timeout: float | None = None) -> None:
    """Shut down the global client. See :meth:`Spanlight.shutdown`."""
    global _client
    with _client_lock:
        client, _client = _client, None
    if client is not None:
        client.shutdown(timeout)
