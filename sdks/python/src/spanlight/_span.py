"""Span and trace data structures.

A :class:`Span` is a timed unit of work (an LLM call, a tool invocation, a
whole request). Spans that share a :class:`TraceState` form one trace; the
trace state carries trace-level fields such as ``user_id`` and ``session_id``
that are attached to every span when it is exported.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from typing import Any, Literal, get_args

from ._ids import new_span_id
from ._serialize import MAX_ATTRIBUTE_STRING_LENGTH, JsonValue, to_jsonable_cut
from ._trace_state import TraceState
from ._usage import SpanUsage

logger = logging.getLogger("spanlight")

SpanKind = Literal["llm", "tool", "retrieval", "chain", "http", "other"]
SpanStatus = Literal["ok", "error", "unset"]

_VALID_KINDS: frozenset[str] = frozenset(get_args(SpanKind))
_MAX_STATUS_MESSAGE = 2_000
_MAX_FINISH_REASON = 64
_REQUEST_HASH_PATTERN = re.compile(r"[0-9a-f]{32}")


def normalize_kind(kind: str) -> SpanKind:
    """Return ``kind`` if it is a known span kind, otherwise ``"other"``."""
    if kind in _VALID_KINDS:
        return kind  # type: ignore[return-value]
    logger.debug("Unknown span kind %r; using 'other'", kind)
    return "other"


def _isoformat_ns(epoch_ns: int) -> str:
    seconds, nanos = divmod(epoch_ns, 1_000_000_000)
    moment = dt.datetime.fromtimestamp(seconds, tz=dt.timezone.utc)
    moment = moment.replace(microsecond=nanos // 1_000)
    return moment.isoformat(timespec="microseconds").replace("+00:00", "Z")


class Span(SpanUsage):
    """A single timed operation within a trace.

    Spans are created by :meth:`Spanlight.span <spanlight.Spanlight.span>`,
    the :func:`~spanlight.observe` decorator and the provider
    wrappers; you normally receive one rather than constructing it. All
    setters are safe to call at any time and never raise. Calls made after
    the span has ended are ignored.

    A span created while the SDK is disabled is *non-recording*: every method
    is a cheap no-op.
    """

    def __init__(
        self,
        *,
        name: str,
        kind: SpanKind,
        trace: TraceState,
        parent_span_id: str | None,
        on_end: Callable[[Span], None] | None,
        recording: bool = True,
        span_id: str | None = None,
    ) -> None:
        self.name = name
        self.kind = kind
        self.trace = trace
        # A fixed id (batch results) makes re-recording the same work idempotent.
        self.span_id = span_id or new_span_id()
        self.parent_span_id = parent_span_id
        self.recording = recording

        self.status: SpanStatus = "unset"
        self.status_message: str | None = None
        self.provider: str | None = None
        self.model: str | None = None
        self._init_usage()
        self.time_to_first_token_ms: float | None = None
        self.request_hash: str | None = None
        self.finish_reason: str | None = None
        self.prompt_version_id: str | None = None
        self.input: JsonValue = None
        self.output: JsonValue = None
        # Whether the SDK cut the input, output or an attribute to fit its bounds; sent as
        # `truncated`.
        self._input_cut = False
        self._output_cut = False
        self._attributes_cut = False
        self.attributes: dict[str, JsonValue] = {}

        self._on_end = on_end
        self._start_epoch_ns = time.time_ns()
        self._start_perf_ns = time.perf_counter_ns()
        self._end_epoch_ns: int | None = None
        self._end_lock = threading.Lock()

    @property
    def trace_id(self) -> str:
        """The 32-hex-character id of the trace this span belongs to."""
        return self.trace.trace_id

    @property
    def is_ended(self) -> bool:
        """Whether :meth:`end` has already been called."""
        return self._end_epoch_ns is not None

    @property
    def _mutable(self) -> bool:
        return self.recording and not self.is_ended

    def set_input(self, value: Any) -> None:
        """Record the operation's input (any JSON-serializable-ish value)."""
        if self._mutable:
            self.input, self._input_cut = to_jsonable_cut(value)

    def set_output(self, value: Any) -> None:
        """Record the operation's output (any JSON-serializable-ish value)."""
        if self._mutable:
            self.output, self._output_cut = to_jsonable_cut(value)

    def set_attribute(self, key: str, value: Any) -> None:
        """Attach a single custom attribute."""
        if self._mutable:
            converted, cut = to_jsonable_cut(value, max_string=MAX_ATTRIBUTE_STRING_LENGTH)
            self.attributes[str(key)] = converted
            self._attributes_cut = self._attributes_cut or cut

    def set_attributes(self, attributes: Mapping[str, Any]) -> None:
        """Attach several custom attributes at once."""
        for key, value in attributes.items():
            self.set_attribute(key, value)

    def set_model(self, model: str | None, *, provider: str | None = None) -> None:
        """Record the model (and optionally the provider) that served the call."""
        if not self._mutable:
            return
        if model:
            self.model = str(model)
        if provider:
            self.provider = str(provider)

    def set_request_hash(self, value: str | None) -> None:
        """Record the request hash used to group identical calls.

        Provider wrappers set this automatically. A value that is not 32
        lowercase hex characters is ignored, because the server would reject
        the whole batch.
        """
        if not self._mutable:
            return
        if value is None or (isinstance(value, str) and _REQUEST_HASH_PATTERN.fullmatch(value)):
            self.request_hash = value
        else:
            logger.debug("Ignoring malformed request hash %r", value)

    def set_finish_reason(self, reason: str | None) -> None:
        """Record why generation stopped, exactly as the provider reports it."""
        if self._mutable and (reason is None or isinstance(reason, str)):
            self.finish_reason = reason[:_MAX_FINISH_REASON] if reason else None

    def set_prompt_version(self, version_id: str | None) -> None:
        """Link the span to the prompt version it used; ``None`` removes the link.

        :class:`~spanlight.CompiledPrompt` does this for you. A value that is
        not a UUID is ignored, because the server would reject the span.
        """
        if not self._mutable:
            return
        if version_id is None:
            self.prompt_version_id = None
            return
        try:
            self.prompt_version_id = str(uuid.UUID(str(version_id)))
        except ValueError:
            logger.debug("Ignoring malformed prompt version id %r", version_id)

    def mark_first_token(self) -> None:
        """Record time-to-first-token as "now"; only the first call counts."""
        if self._mutable and self.time_to_first_token_ms is None:
            elapsed_ns = time.perf_counter_ns() - self._start_perf_ns
            self.time_to_first_token_ms = elapsed_ns / 1_000_000

    def record_error(self, error: BaseException | str) -> None:
        """Mark the span as failed.

        Args:
            error: The exception that occurred, or a human-readable message.
        """
        if not self._mutable:
            return
        self.status = "error"
        if isinstance(error, BaseException):
            message = str(error) or type(error).__name__
            self.attributes["error.type"] = type(error).__name__
        else:
            message = error
        self.status_message = message[:_MAX_STATUS_MESSAGE]

    def end(self) -> None:
        """Finish the span and hand it to the exporter. Idempotent."""
        with self._end_lock:
            if self._end_epoch_ns is not None:
                return
            elapsed_ns = time.perf_counter_ns() - self._start_perf_ns
            self._end_epoch_ns = self._start_epoch_ns + elapsed_ns
        if self.status == "unset":
            self.status = "ok"
        if self.recording and self._on_end is not None:
            try:
                self._on_end(self)
            except Exception:
                logger.debug("Failed to export span %s", self.span_id, exc_info=True)

    def to_payload(self) -> dict[str, JsonValue]:
        """Serialize the span into the ``POST /v1/traces`` span schema."""
        end_ns = self._end_epoch_ns if self._end_epoch_ns is not None else time.time_ns()
        payload: dict[str, JsonValue] = {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "name": self.name,
            "kind": self.kind,
            "status": self.status,
            "status_message": self.status_message,
            "start_time": _isoformat_ns(self._start_epoch_ns),
            "end_time": _isoformat_ns(end_ns),
            "provider": self.provider,
            "model": self.model,
            "usage": self._usage_payload(),
            "time_to_first_token_ms": self.time_to_first_token_ms,
            "request_hash": self.request_hash,
            "finish_reason": self.finish_reason,
            "input": self.input,
            "output": self.output,
            "attributes": dict(self.attributes),
            "trace": self.trace.to_payload(),
        }
        # Sent only when set, so payloads for servers that predate prompts stay unchanged.
        if self.prompt_version_id is not None:
            payload["prompt_version_id"] = self.prompt_version_id
        if self._input_cut or self._output_cut or self._attributes_cut:
            payload["truncated"] = True
        return payload

    def __repr__(self) -> str:
        return (
            f"Span(name={self.name!r}, kind={self.kind!r}, trace_id={self.trace_id!r}, "
            f"span_id={self.span_id!r}, status={self.status!r})"
        )
