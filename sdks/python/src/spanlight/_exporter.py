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

Spans and scores share one queue (and so one ``max_queue`` bound). Each cycle
takes up to ``batch_size`` items, sends the spans first and the scores after,
so a score recorded after its span never reaches the server before it.
"""

from __future__ import annotations

import email.utils
import gzip
import json
import logging
import os
import random
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from ._serialize import JsonValue
from ._version import __version__

logger = logging.getLogger("spanlight")

SpanPayload = dict[str, JsonValue]

_MAX_LOGGED_REJECTIONS = 10


@dataclass(frozen=True)
class _Target:
    """One endpoint the exporter delivers to."""

    url: str
    body_key: str  # the list's key in the request body, and the plural noun in logs
    id_field: str  # the payload field logged when the server rejects an item


_SPANS = "spans"
_SCORES = "scores"
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
        gzip_body: bool = False,
        timeout: float = 10.0,
        max_retries: int = 5,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] | None = None,
        jitter: Callable[[], float] = random.random,
        base_backoff: float = 0.5,
        max_backoff: float = 30.0,
    ) -> None:
        self._targets: dict[str, _Target] = {_SPANS: _Target(url, _SPANS, "span_id")}
        if scores_url is not None:
            self._targets[_SCORES] = _Target(scores_url, _SCORES, "id")
        self._scores_unsupported_logged = False
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": f"spanlight-python/{__version__}",
        }
        if gzip_body:
            self._headers["Content-Encoding"] = "gzip"
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self._max_queue = max_queue
        self._gzip = gzip_body
        self._timeout = timeout
        self._max_retries = max_retries
        self._transport = transport
        self._sleep = sleep if sleep is not None else self._interruptible_sleep
        self._jitter = jitter
        self._base_backoff = base_backoff
        self._max_backoff = max_backoff

        self._condition = threading.Condition()
        self._queue: deque[_Item] = deque()
        self._in_flight = 0
        self._flush_requested = False
        self._closed = False
        self._abort_retries = threading.Event()
        self._worker: threading.Thread | None = None
        self._client: httpx.Client | None = None
        self._pid = os.getpid()

        self._dropped = 0
        self._failed = 0
        self._sent = 0

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
        return self._failed

    @property
    def sent_count(self) -> int:
        """Spans and scores delivered and accepted by the server."""
        return self._sent

    def export(self, span: SpanPayload) -> None:
        """Enqueue one serialized span. Never blocks on I/O and never raises."""
        self._enqueue(_SPANS, span)

    def export_score(self, score: SpanPayload) -> None:
        """Enqueue one serialized score. Never blocks on I/O and never raises."""
        if _SCORES not in self._targets:
            logger.debug("No score endpoint configured; dropping a score")
            return
        self._enqueue(_SCORES, score)

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
            self._abort_retries.set()
            worker = self._worker
            if worker is not None and worker is not threading.current_thread():
                worker.join(max(deadline - time.monotonic(), 0.0))
            if self._client is not None:
                self._client.close()
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
        self._client = None

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
                for kind in (_SPANS, _SCORES):
                    payloads = [payload for item_kind, payload in batch if item_kind == kind]
                    if payloads:
                        self._send_guarded(self._targets[kind], payloads)
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

    def _http_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(transport=self._transport, timeout=self._timeout)
        return self._client

    def _send_guarded(self, target: _Target, batch: list[SpanPayload]) -> None:
        """Send one target's part of a cycle; a crash here never stops the next part."""
        try:
            self._send_batch(target, batch)
        except Exception:
            self._failed += len(batch)
            logger.warning(
                "Unexpected error exporting %d %s", len(batch), target.body_key, exc_info=True
            )

    def _send_batch(self, target: _Target, batch: list[SpanPayload]) -> None:
        if not batch:
            return
        body = json.dumps({target.body_key: batch}, separators=(",", ":"), default=str).encode()
        if self._gzip:
            body = gzip.compress(body)

        for attempt in range(self._max_retries + 1):
            retry_after: float | None = None
            try:
                response = self._http_client().post(target.url, content=body, headers=self._headers)
            except httpx.HTTPError as exc:
                failure = f"{type(exc).__name__}: {exc}"
            else:
                status = response.status_code
                if 200 <= status < 300:
                    self._handle_success(target, response, batch)
                    return
                if status == 413 and len(batch) > 1:
                    self._split_and_send(target, batch)
                    return
                if not _is_retryable(status):
                    self._failed += len(batch)
                    self._log_rejected_batch(target, response, len(batch))
                    return
                failure = f"HTTP {status}"
                retry_after = parse_retry_after(response.headers.get("Retry-After"))

            if attempt >= self._max_retries or self._abort_retries.is_set():
                self._failed += len(batch)
                logger.warning(
                    "Dropping %d %s after %d attempts (%s)",
                    len(batch),
                    target.body_key,
                    attempt + 1,
                    failure,
                )
                return
            delay = self._retry_delay(attempt, retry_after)
            logger.debug("Export failed (%s); retrying in %.2fs", failure, delay)
            self._sleep(delay)

    def _split_and_send(self, target: _Target, batch: list[SpanPayload]) -> None:
        """Retry an oversized (HTTP 413) batch as two smaller ones."""
        middle = len(batch) // 2
        self._send_batch(target, batch[:middle])
        self._send_batch(target, batch[middle:])

    def _log_rejected_batch(self, target: _Target, response: httpx.Response, size: int) -> None:
        if target.body_key == _SCORES and response.status_code == 404:
            # A server older than score ingestion: say so once instead of on every batch.
            if not self._scores_unsupported_logged:
                self._scores_unsupported_logged = True
                logger.warning(
                    "This Spanlight server does not accept scores (HTTP 404); dropping them"
                )
            return
        logger.error(
            "Spanlight rejected a batch of %d %s: HTTP %d %s",
            size,
            target.body_key,
            response.status_code,
            _short_body(response),
        )

    def _handle_success(
        self, target: _Target, response: httpx.Response, batch: list[SpanPayload]
    ) -> None:
        batch_size = len(batch)
        try:
            result: Any = response.json()
        except ValueError:
            self._sent += batch_size
            return
        rejected = result.get("rejected") if isinstance(result, dict) else None
        if not isinstance(rejected, list) or not rejected:
            self._sent += batch_size
            return

        self._sent += batch_size - len(rejected)
        self._failed += len(rejected)
        logger.warning(
            "Spanlight rejected %d of %d %s",
            len(rejected),
            batch_size,
            target.body_key,
        )
        for item in rejected[:_MAX_LOGGED_REJECTIONS]:
            if isinstance(item, dict):
                index = item.get("index")
                sent = batch[index] if isinstance(index, int) and 0 <= index < batch_size else {}
                logger.warning(
                    "  %s %s (index %s): %s",
                    target.body_key[:-1],
                    item.get(target.id_field) or sent.get(target.id_field),
                    index,
                    item.get("reason"),
                )

    def _retry_delay(self, attempt: int, retry_after: float | None) -> float:
        """Return seconds to wait before retry number ``attempt + 1``.

        ``Retry-After`` wins when present. Otherwise use "equal jitter"
        exponential backoff: half of the exponential step is fixed and the
        other half random, which spreads retries out without ever retrying
        immediately.
        """
        if retry_after is not None:
            return min(max(retry_after, 0.0), self._max_backoff)
        step = min(self._max_backoff, self._base_backoff * (2.0**attempt))
        return step / 2 + self._jitter() * step / 2

    def _interruptible_sleep(self, seconds: float) -> None:
        self._abort_retries.wait(seconds)


def _is_retryable(status: int) -> bool:
    return status == 429 or status >= 500


def parse_retry_after(value: str | None) -> float | None:
    """Parse a ``Retry-After`` header (delta-seconds or HTTP-date) into seconds."""
    if value is None:
        return None
    value = value.strip()
    try:
        return max(float(value), 0.0)
    except ValueError:
        pass
    try:
        moment = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return max(moment.timestamp() - time.time(), 0.0)


def _short_body(response: httpx.Response) -> str:
    try:
        return response.text[:500]
    except Exception:
        return "<unreadable body>"
