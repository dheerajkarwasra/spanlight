"""W3C Trace Context propagation across processes: MCP ``_meta``, HTTP headers, queues.

:func:`inject` writes the current span as a ``traceparent`` into a carrier dict, and
:func:`continue_trace` makes the span a ``traceparent`` names the parent of the spans opened
inside its block. ``tracestate`` and ``baggage`` are left as they are. The W3C sampled flag
is not honoured: the SDK records every span.

Only continue traces from callers you trust: a caller chooses the trace id, so it can attach
your spans to another trace of the same project.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from contextvars import Token
from types import TracebackType
from typing import Any

from ._context import get_current_span, restore_current_span, set_current_span
from ._span import Span
from ._trace_state import TraceState

logger = logging.getLogger("spanlight")

_TRACEPARENT = re.compile(r"([0-9a-f]{2})-([0-9a-f]{32})-([0-9a-f]{16})-[0-9a-f]{2}")


def inject(carrier: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return a copy of ``carrier`` with the current span as its ``traceparent``.

    A ``traceparent`` already in the carrier is kept. Outside a recording span the copy is
    returned unchanged.

    Example:
        >>> params["_meta"] = spanlight.inject(params.get("_meta"))  # doctest: +SKIP
    """
    result = dict(carrier or {})
    span = get_current_span()
    if span is not None and span.recording and "traceparent" not in result:
        result["traceparent"] = f"00-{span.trace_id}-{span.span_id}-01"
    return result


def _parse(value: object) -> tuple[str, str] | None:
    """``(trace_id, parent_span_id)`` of a valid W3C ``traceparent``, else ``None``."""
    if not isinstance(value, str):
        return None
    match = _TRACEPARENT.match(value)
    if match is None:
        return None
    version, trace_id, span_id = match.groups()
    # Version 00 is exactly 55 characters; a later version may add fields after a dash.
    exact = len(value) == 55
    if version == "ff" or not (exact or (version != "00" and value[55] == "-")):
        return None
    if not trace_id.strip("0") or not span_id.strip("0"):
        return None
    return trace_id, span_id


class continue_trace:  # lowercase: used like a function, as `contextlib` helpers are
    """Continue the trace named by ``carrier["traceparent"]`` inside a ``with`` block.

    Spans opened inside the block become children of the remote span. A missing or invalid
    ``traceparent`` is ignored, so the next span starts a new trace. Works with ``with`` and
    ``async with``. The carrier is anything with ``.get`` (a dict, request headers) or an object
    with a ``traceparent`` attribute (the MCP Python SDK's ``request.params.meta``).

    The remote parent is a placeholder that is never sent; :func:`get_current_span` returns it
    inside the block, and setters called on it are lost.
    """

    def __init__(self, carrier: Mapping[str, Any] | object | None) -> None:
        try:
            # `.get` covers dicts and framework header objects that are not a `Mapping`.
            get = getattr(carrier, "get", None)
            value = get("traceparent") if callable(get) else getattr(carrier, "traceparent", None)
            self._ids = _parse(value)
        except Exception:
            logger.debug("continue_trace() could not read the carrier", exc_info=True)
            self._ids = None
        self._token: Token[Span | None] | None = None
        self._previous: Span | None = None

    def __enter__(self) -> None:
        if self._ids is None:
            return
        trace_id, span_id = self._ids
        # No name, environment or release: the caller's trace fields must win on the server.
        placeholder = Span(
            name="remote parent",
            kind="other",
            trace=TraceState(trace_id=trace_id),
            parent_span_id=None,
            on_end=None,
            span_id=span_id,
        )
        self._previous = get_current_span()
        self._token = set_current_span(placeholder)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._token is not None:
            restore_current_span(self._token, self._previous)
            self._token = None

    async def __aenter__(self) -> None:
        self.__enter__()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.__exit__(exc_type, exc, traceback)
