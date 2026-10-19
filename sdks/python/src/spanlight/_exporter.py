"""Background batching exporter for ``POST /v1/traces`` and ``POST /v1/scores``.

Design goals, in priority order:

1. **Never hurt the host application.** :meth:`BatchExporter.export` only
   appends to an in-memory queue under a lock; all network I/O happens on a
   daemon thread. Nothing in this module raises into caller code.
2. **Bounded memory.** The queue holds at most ``max_queue`` spans. When it is
   full the *oldest* span is dropped (recent data is usually more useful) and
   the drop is counted.
3. **Deliver what we can.** Batches are retried with jittered exponential
   backoff on network errors, ``429`` and ``5xx``, honouring ``Retry-After``.
   Other ``4xx`` responses are permanent failures and are logged, not retried.
   The sending itself lives in :mod:`spanlight._exporter_delivery`.

Spans and scores share one queue (and so one ``max_queue`` bound). Each cycle
takes up to ``batch_size`` items, sends the spans first and the scores after,
so a score recorded after its span never reaches the server before it.
"""

from __future__ import annotations

import logging
import os
import random
import secrets
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Any

import httpx

from ._exporter_delivery import SCORES, SPANS, Delivery, SpanPayload, parse_retry_after
from ._version import __version__

__all__ = ["BatchExporter", "SpanPayload", "parse_retry_after"]

logger = logging.getLogger("spanlight")

_Item = tuple[str, SpanPayload]


class BatchExporter:
    """Queue spans and scores in memory and ship them to the ingestion API in batches.

    Args:
        url: Full ingestion URL, e.g. ``http://localhost:8000/v1/traces``.
        scores_url: Full score ingestion URL, e.g. ``http://localhost:8000/v1/scores``.
            Without it, scores are dropped.
        api_key: Project API key sent as a Bearer token.
        batch_size: Maximum spans per request. A full batch is sent at once.
        flush_interval: Maximum seconds a span waits before being sent.
        max_queue: Maximum spans buffered in memory.
        gzip_body: Compress request bodies with gzip.
        timeout: Per-request timeout in seconds.
        max_retries: Retries per batch after the first attempt.
        transport: Optional custom ``httpx`` transport (useful for tests and
            proxies).
        sleep: Function used to wait between retries. Defaults to a wait that
            is interrupted by :meth:`shutdown`.
        jitter: Function returning a float in ``[0, 1)`` used to randomize
            backoff delays.
        base_backoff: Backoff before the first retry, in seconds.
        max_backoff: Upper bound for any single wait, in seconds.
    """

    def __init__(
        self,
        *,
        url: str,
        api_key: str,
        scores_url: str | None = None,
        batch_size: int = 100,
        flush_interval: float = 2.0,
        max_queue: int = 10_000,
        gzip_body: bool = True,
        timeout: float = 10.0,
        max_retries: int = 5,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] | None = None,
        jitter: Callable[[], float] = random.random,
        base_backoff: float = 0.5,
        max_backoff: float = 30.0,
    ) -> None:
        self._delivery = Delivery(
            url=url,
            api_key=api_key,
            scores_url=scores_url,
            gzip_body=gzip_body,
            timeout=timeout,
            max_retries=max_retries,
            transport=transport,
            sleep=sleep,
            jitter=jitter,
            base_backoff=base_backoff,
            max_backoff=max_backoff,
        )
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self._max_queue = max_queue

        self._condition = threading.Condition()
        self._queue: deque[_Item] = deque()
        self._in_flight = 0
        self._flush_requested = False
        self._closed = False
        self._worker: threading.Thread | None = None
        self._pid = os.getpid()
        # Names this exporter in the counters it reports; a forked child gets its own.
        self._exporter_id = secrets.token_hex(16)

        self._dropped = 0

    # ------------------------------------------------------------------ #
    # Public API (called from user threads)
    # ------------------------------------------------------------------ #

    @property
    def dropped_count(self) -> int:
        """Spans and scores discarded because the queue was full."""
        return self._dropped

    @property
    def failed_count(self) -> int:
        """Spans and scores lost because their batch could not be delivered."""
        return self._delivery.failed

    @property
    def client_counters(self) -> dict[str, Any]:
        """The ``client`` object sent with every spans batch.

        Totals are cumulative, not deltas: a batch can be delivered twice, and
        the server derives the delta from the last total of this ``exporter_id``.
        """
        return {
            "sdk": "spanlight-python",
            "version": __version__,
            "exporter_id": self._exporter_id,
            "dropped_total": self._dropped,
            "failed_total": self._delivery.failed,
        }

    @property
    def sent_count(self) -> int:
        """Spans and scores delivered and accepted by the server."""
        return self._delivery.sent

    def export(self, span: SpanPayload) -> None:
        """Enqueue one serialized span. Never blocks on I/O and never raises."""
        self._enqueue(SPANS, span)

    def export_score(self, score: SpanPayload) -> None:
        """Enqueue one serialized score. Never blocks on I/O and never raises."""
        if not self._delivery.accepts(SCORES):
            logger.debug("No score endpoint configured; dropping a score")
            return
        self._enqueue(SCORES, score)

    def _enqueue(self, kind: str, payload: SpanPayload) -> None:
        try:
            self._check_fork()
            with self._condition:
                if self._closed:
                    return
                if len(self._queue) >= self._max_queue:
                    self._queue.popleft()
                    self._dropped += 1
                    if self._dropped == 1:
                        logger.warning(
                            "Spanlight queue is full (%d items); dropping the oldest",
                            self._max_queue,
                        )
                self._queue.append((kind, payload))
                if len(self._queue) >= self._batch_size:
                    self._condition.notify_all()
            self._ensure_worker()
        except Exception:
            logger.debug("Failed to enqueue %s", kind, exc_info=True)

    def flush(self, timeout: float | None = None) -> bool:
        """Send everything queued so far and wait until it is delivered.

        Args:
            timeout: Maximum seconds to wait; ``None`` waits indefinitely.

        Returns:
            ``True`` if the queue drained in time, ``False`` otherwise.
        """
        try:
            self._ensure_worker()
            deadline = None if timeout is None else time.monotonic() + timeout
            with self._condition:
                self._flush_requested = True
                self._condition.notify_all()
                while self._queue or self._in_flight:
                    if self._worker is None or not self._worker.is_alive():
                        return False
                    remaining = None if deadline is None else deadline - time.monotonic()
                    if remaining is not None and remaining <= 0:
                        return False
                    self._condition.wait(remaining)
            return True
        except Exception:
            logger.debug("Flush failed", exc_info=True)
            return False

    def shutdown(self, timeout: float = 5.0) -> None:
        """Flush remaining spans (bounded by ``timeout``) and stop the worker.

        Spans that cannot be delivered within ``timeout`` are discarded so the
        interpreter can exit promptly. Calling ``shutdown`` twice is harmless.
        """
        try:
            deadline = time.monotonic() + timeout
            self.flush(timeout)
            with self._condition:
                self._closed = True
                self._condition.notify_all()
            self._delivery.abort_retries()
            worker = self._worker
            if worker is not None and worker is not threading.current_thread():
                worker.join(max(deadline - time.monotonic(), 0.0))
            self._delivery.close()
        except Exception:
            logger.debug("Shutdown failed", exc_info=True)

    # ------------------------------------------------------------------ #
    # Worker thread
    # ------------------------------------------------------------------ #

    def _check_fork(self) -> None:
        """Reset state in a forked child, where the parent's thread does not exist."""
        if os.getpid() == self._pid:
            return
        self._pid = os.getpid()
        self._condition = threading.Condition()
        self._queue = deque()
        self._in_flight = 0
        self._worker = None
        self._delivery.forget_client()
        # A new exporter: the parent keeps reporting its totals, so start from zero.
        self._exporter_id = secrets.token_hex(16)
        self._dropped = 0
        self._delivery.reset_counters()

    def _ensure_worker(self) -> None:
        with self._condition:
            if self._closed:
                return
            if self._worker is not None and self._worker.is_alive():
                return
            self._worker = threading.Thread(
                target=self._run,
                name="spanlight-exporter",
                daemon=True,
            )
            self._worker.start()

    def _run(self) -> None:
        while True:
            batch = self._next_batch()
            if batch is None:
                return
            try:
                for kind in (SPANS, SCORES):
                    payloads = [payload for item_kind, payload in batch if item_kind == kind]
                    if payloads:
                        client = self.client_counters if kind == SPANS else None
                        self._delivery.send(kind, payloads, client)
            finally:
                with self._condition:
                    self._in_flight -= len(batch)
                    self._condition.notify_all()

    def _next_batch(self) -> list[_Item] | None:
        """Block until a batch is due; return ``None`` when the exporter is closed."""
        with self._condition:
            deadline = time.monotonic() + self._flush_interval
            while self._should_wait():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._condition.wait(remaining)

            if not self._queue:
                self._flush_requested = False
                self._condition.notify_all()
                return None if self._closed else []

            size = min(self._batch_size, len(self._queue))
            batch = [self._queue.popleft() for _ in range(size)]
            self._in_flight += len(batch)
            return batch

    def _should_wait(self) -> bool:
        if self._closed or self._flush_requested:
            return False
        return len(self._queue) < self._batch_size
