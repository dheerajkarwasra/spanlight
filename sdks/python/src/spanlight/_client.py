"""The :class:`Spanlight` client: configuration, span creation, export, prompts and scores."""

from __future__ import annotations

import atexit
import logging
from collections.abc import Callable, Iterable, Mapping
from contextvars import Token
from types import TracebackType
from typing import Any, ParamSpec, TypeVar, overload

import httpx

from ._config import SpanlightConfig, resolve_config
from ._context import (
    get_current_prompt_version_id,
    get_current_span,
    restore_current_span,
    set_current_span,
)
from ._exporter import BatchExporter
from ._prompt_client import PromptClient
from ._scores import ScoreValue, build_score
from ._span import Span, SpanKind, normalize_kind
from ._trace_state import TraceState

logger = logging.getLogger("spanlight")

P = ParamSpec("P")
R = TypeVar("R")


class Spanlight:
    """Spanlight client.

    A ``Spanlight`` client owns the configuration and the background
    exporter. Most applications create one at startup with
    :func:`spanlight.init` and then use the module-level helpers; creating
    instances directly is useful for tests or for sending to several
    projects from one process.

    Every argument left as ``None`` is read from the environment:

    ===================  =========================  ==========================
    Argument             Environment variable       Default
    ===================  =========================  ==========================
    ``api_key``          ``SPANLIGHT_API_KEY``      none (export disabled)
    ``host``             ``SPANLIGHT_HOST``         ``http://localhost:8000``
    ``environment``      ``SPANLIGHT_ENVIRONMENT``  none
    ``release``          ``SPANLIGHT_RELEASE``      none
    ``enabled``          ``SPANLIGHT_ENABLED``      ``True``
    ===================  =========================  ==========================

    Args:
        api_key: Project API key (``spl_live_...``).
        host: Base URL of the Spanlight API.
        environment: Deployment environment, e.g. ``"production"``.
        release: Application version, e.g. a git SHA.
        enabled: Set ``False`` to turn tracing and scores into a no-op. Prompts are
            still fetched when an API key is set, because they are application data.
        flush_interval: Maximum seconds a span waits before it is sent.
        batch_size: Maximum spans per request (capped at 1000).
        max_queue: Maximum spans buffered in memory; oldest dropped first.
        gzip: Compress request bodies.
        timeout: HTTP timeout per request, in seconds.
        max_retries: Retries per batch for network errors, 429 and 5xx.
        shutdown_timeout: Seconds allowed for the final flush at exit.
        prompt_cache_ttl: Seconds a fetched prompt is served before it is revalidated.
        transport: Custom ``httpx`` transport (advanced; mainly for tests).
    """

    def __init__(
        self,
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
    ) -> None:
        self.config: SpanlightConfig = resolve_config(
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
        )
        self._exporter: BatchExporter | None = None
        self.prompts: PromptClient = PromptClient(
            base_url=self.config.prompts_url,
            api_key=self.config.api_key,
            timeout=self.config.timeout,
            cache_ttl=self.config.prompt_cache_ttl,
            transport=transport,
        )
        """Fetches prompts from Spanlight: ``client.prompts.get("support/answer")``."""

        if self.config.enabled and not self.config.api_key:
            logger.warning(
                "Spanlight: no API key configured (set SPANLIGHT_API_KEY or pass "
                "api_key=...); tracing is disabled."
            )
        elif self.config.enabled and self.config.api_key:
            self._exporter = BatchExporter(
                url=self.config.ingest_url,
                api_key=self.config.api_key,
                scores_url=self.config.scores_url,
                batch_size=self.config.batch_size,
                flush_interval=self.config.flush_interval,
                max_queue=self.config.max_queue,
                gzip_body=self.config.gzip,
                timeout=self.config.timeout,
                max_retries=self.config.max_retries,
                transport=transport,
            )
            atexit.register(self._flush_at_exit)

    # ------------------------------------------------------------------ #
    # State
    # ------------------------------------------------------------------ #

    @property
    def enabled(self) -> bool:
        """Whether spans are recorded and exported."""
        return self._exporter is not None

    @property
    def exporter(self) -> BatchExporter | None:
        """The background exporter, or ``None`` when disabled."""
        return self._exporter

    # ------------------------------------------------------------------ #
    # Spans
    # ------------------------------------------------------------------ #

    def start_span(
        self,
        name: str,
        *,
        kind: SpanKind = "chain",
        parent: Span | None = None,
    ) -> Span:
        """Create and start a span without making it current.

        Most code should prefer :meth:`span` or :func:`observe`, which also
        manage the current-span context and end the span for you. Use this
        for spans whose lifetime does not match a block of code; remember to
        call :meth:`Span.end`.

        Args:
            name: Human-readable operation name.
            kind: One of ``llm``, ``tool``, ``retrieval``, ``chain``,
                ``http`` or ``other``.
            parent: Parent span; defaults to the current span. Without a
                parent the span starts a new trace.

        Returns:
            The started span (non-recording when the SDK is disabled).
        """
        try:
            parent = parent if parent is not None else get_current_span()
            if parent is not None:
                trace = parent.trace
                parent_span_id: str | None = parent.span_id
            else:
                trace = TraceState(
                    name=name,
                    environment=self.config.environment,
                    release=self.config.release,
                )
                parent_span_id = None
            span = Span(
                name=name,
                kind=normalize_kind(kind),
                trace=trace,
                parent_span_id=parent_span_id,
                on_end=self._export_span,
                recording=self.enabled,
            )
            prompt_version_id = get_current_prompt_version_id()
            if prompt_version_id is not None:
                span.set_prompt_version(prompt_version_id)
            return span
        except Exception:
            logger.debug("Failed to start span %r", name, exc_info=True)
            return _non_recording_span(name)

    def span(
        self,
        name: str,
        *,
        kind: SpanKind = "chain",
        input: Any = None,
        attributes: Mapping[str, Any] | None = None,
    ) -> SpanContext:
        """Trace a block of code as a span.

        Works with both ``with`` and ``async with``. The span becomes the
        current span inside the block, so nested spans and wrapped LLM calls
        attach to it. An exception escaping the block marks the span as
        failed and is re-raised unchanged.

        Example:
            >>> with tracer.span("retrieve", kind="retrieval") as span:  # doctest: +SKIP
            ...     docs = search(query)
            ...     span.set_output(docs)

        Args:
            name: Human-readable operation name.
            kind: Span kind (see :meth:`start_span`).
            input: Optional input recorded on the span immediately.
            attributes: Optional custom attributes.

        Returns:
            A context manager yielding the :class:`Span`.
        """
        return SpanContext(self, name, kind=kind, input=input, attributes=attributes)

    @overload
    def observe(self, func: Callable[P, R], /) -> Callable[P, R]: ...

    @overload
    def observe(
        self,
        name: str | None = None,
        *,
        kind: SpanKind = "chain",
        capture_input: bool = True,
        capture_output: bool = True,
    ) -> Callable[[Callable[P, R]], Callable[P, R]]: ...

    def observe(
        self,
        name: Callable[P, R] | str | None = None,
        *,
        kind: SpanKind = "chain",
        capture_input: bool = True,
        capture_output: bool = True,
    ) -> Callable[P, R] | Callable[[Callable[P, R]], Callable[P, R]]:
        """Decorator tracing every call of a function with this client.

        Identical to :func:`spanlight.observe` but bound to this
        ``Spanlight`` instead of the global client.
        """
        from ._decorator import build_observe

        return build_observe(
            lambda: self,
            name,
            kind=kind,
            capture_input=capture_input,
            capture_output=capture_output,
        )

    def update_trace(
        self,
        *,
        name: str | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
        tags: Iterable[str] | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> None:
        """Set fields on the current trace. See :func:`spanlight.update_trace`."""
        update_trace(
            name=name, user_id=user_id, session_id=session_id, tags=tags, metadata=metadata
        )

    # ------------------------------------------------------------------ #
    # Scores
    # ------------------------------------------------------------------ #

    def score(
        self,
        trace_id: str,
        name: str,
        value: ScoreValue,
        *,
        span_id: str | None = None,
        comment: str | None = None,
    ) -> None:
        """Record a score on a trace (or one of its spans). Never raises.

        Scores are sent in the background with the spans, after the spans
        of the same cycle. A ``bool`` becomes a boolean score, a ``str`` a
        categorical one, any other number a numeric one.

        Args:
            trace_id: The trace to score, e.g. ``spanlight.get_current_trace_id()``.
            name: The score name, e.g. ``"helpfulness"``.
            value: The score: a number, ``True``/``False`` or a category.
            span_id: Score one span of the trace instead of the whole trace.
            comment: Free-text note shown next to the score.
        """
        exporter = self._exporter
        if exporter is None:
            return
        try:
            payload = build_score(trace_id, name, value, span_id=span_id, comment=comment)
            if payload is not None:
                exporter.export_score(payload)
        except Exception:
            logger.debug("Failed to record score %r", name, exc_info=True)

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def flush(self, timeout: float | None = None) -> bool:
        """Block until every span ended and score recorded so far has been sent.

        Call this at the end of short-lived scripts, serverless handlers and
        tests. Long-running servers do not need it.

        Args:
            timeout: Maximum seconds to wait; ``None`` waits indefinitely.

        Returns:
            ``True`` if everything was delivered (or the SDK is disabled).
        """
        if self._exporter is None:
            return True
        return self._exporter.flush(timeout)

    def shutdown(self, timeout: float | None = None) -> None:
        """Flush and stop the background exporter. The client is disabled afterwards.

        Args:
            timeout: Maximum seconds to spend; defaults to ``shutdown_timeout``.
        """
        self.prompts.close()
        exporter = self._exporter
        if exporter is None:
            return
        self._exporter = None
        atexit.unregister(self._flush_at_exit)
        exporter.shutdown(self.config.shutdown_timeout if timeout is None else timeout)

    def _flush_at_exit(self) -> None:
        self.shutdown()

    def _export_span(self, span: Span) -> None:
        exporter = self._exporter
        if exporter is not None:
            exporter.export(span.to_payload())

    def __repr__(self) -> str:
        return f"Spanlight(host={self.config.host!r}, enabled={self.enabled})"


class SpanContext:
    """Context manager returned by :meth:`Spanlight.span` (sync and async)."""

    def __init__(
        self,
        client: Spanlight,
        name: str,
        *,
        kind: SpanKind,
        input: Any,
        attributes: Mapping[str, Any] | None,
    ) -> None:
        self._client = client
        self._name = name
        self._kind = kind
        self._input = input
        self._attributes = attributes
        self._span: Span | None = None
        self._previous: Span | None = None
        self._token: Token[Span | None] | None = None

    def __enter__(self) -> Span:
        span = self._client.start_span(self._name, kind=self._kind)
        if self._input is not None:
            span.set_input(self._input)
        if self._attributes:
            span.set_attributes(self._attributes)
        self._span = span
        self._previous = get_current_span()
        self._token = set_current_span(span)
        return span

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        span = self._span
        if span is None:
            return
        if self._token is not None:
            restore_current_span(self._token, self._previous)
        if exc is not None and not isinstance(exc, GeneratorExit):
            span.record_error(exc)
        span.end()

    async def __aenter__(self) -> Span:
        return self.__enter__()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.__exit__(exc_type, exc, traceback)


def update_trace(
    *,
    name: str | None = None,
    user_id: str | None = None,
    session_id: str | None = None,
    tags: Iterable[str] | None = None,
    metadata: Mapping[str, object] | None = None,
) -> None:
    """Attach trace-level fields to the trace of the current span.

    Fields left as ``None`` are unchanged; tags are added to the existing
    set. Has no effect (beyond a debug log) when called outside any span.

    Args:
        name: Trace name shown in the UI (defaults to the root span name).
        user_id: Your application's user identifier.
        session_id: Groups traces into a conversation/session.
        tags: Free-form labels for filtering.
        metadata: Key-value pairs such as ``{"customer_id": "acme"}``; a later value
            replaces a key's earlier one. Invalid entries are dropped with a debug log.
    """
    span = get_current_span()
    if span is None:
        logger.debug("update_trace() called outside of a span; ignoring")
        return
    try:
        span.trace.update(
            name=name, user_id=user_id, session_id=session_id, tags=tags, metadata=metadata
        )
    except Exception:
        logger.debug("update_trace() failed", exc_info=True)


def _non_recording_span(name: str) -> Span:
    return Span(
        name=name,
        kind="other",
        trace=TraceState(),
        parent_span_id=None,
        on_end=None,
        recording=False,
    )
