"""Delivery of one batch to the ingestion API: retries, backoff, splitting and logging.

Runs only on the exporter's worker thread. Batches are retried with jittered
exponential backoff on network errors, ``429`` and ``5xx``, honouring
``Retry-After``. Other ``4xx`` responses are permanent failures and are logged,
not retried. Nothing in this module raises into the caller.
"""

from __future__ import annotations

import email.utils
import gzip
import json
import logging
import math
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import httpx

from ._serialize import JsonValue
from ._version import __version__

logger = logging.getLogger("spanlight")

SpanPayload = dict[str, JsonValue]

SPANS = "spans"
SCORES = "scores"

_MAX_LOGGED_REJECTIONS = 10


@dataclass(frozen=True)
class _Target:
    """One endpoint the exporter delivers to."""

    url: str
    body_key: str  # the list's key in the request body, and the plural noun in logs
    id_field: str  # the payload field logged when the server rejects an item


class Delivery:
    """Send batches of spans or scores, and count what was accepted and what was lost.

    The arguments are those of :class:`spanlight._exporter.BatchExporter` that
    concern the network.
    """

    def __init__(
        self,
        *,
        url: str,
        api_key: str,
        scores_url: str | None,
        gzip_body: bool,
        timeout: float,
        max_retries: int,
        transport: httpx.BaseTransport | None,
        sleep: Callable[[float], None] | None,
        jitter: Callable[[], float],
        base_backoff: float,
        max_backoff: float,
    ) -> None:
        self._targets: dict[str, _Target] = {SPANS: _Target(url, SPANS, "span_id")}
        if scores_url is not None:
            self._targets[SCORES] = _Target(scores_url, SCORES, "id")
        self._scores_unsupported_logged = False
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": f"spanlight-python/{__version__}",
        }
        if gzip_body:
            self._headers["Content-Encoding"] = "gzip"
        self._gzip = gzip_body
        self._timeout = timeout
        self._max_retries = max_retries
        self._transport = transport
        self._sleep = sleep if sleep is not None else self._interruptible_sleep
        self._jitter = jitter
        self._base_backoff = base_backoff
        self._max_backoff = max_backoff
        self._abort_retries = threading.Event()
        self._client: httpx.Client | None = None
        self.failed = 0
        self.sent = 0

    def accepts(self, kind: str) -> bool:
        """Whether an endpoint is configured for ``kind`` (``SPANS`` or ``SCORES``)."""
        return kind in self._targets

    def send(
        self, kind: str, batch: list[SpanPayload], client: Mapping[str, Any] | None = None
    ) -> None:
        """Send one kind's part of a cycle; a crash here never stops the next part.

        ``client`` is the exporter's counters object, sent beside the spans only.
        """
        target = self._targets[kind]
        try:
            self._send_batch(target, batch, client)
        except Exception:
            self.failed += len(batch)
            logger.warning(
                "Unexpected error exporting %d %s", len(batch), target.body_key, exc_info=True
            )

    def abort_retries(self) -> None:
        """Stop waiting between retries: the batch in progress gets no further attempt."""
        self._abort_retries.set()

    def reset_counters(self) -> None:
        """Start the delivery counters from zero (in a forked child)."""
        self.failed = 0
        self.sent = 0

    def forget_client(self) -> None:
        """Drop the HTTP client without closing it (it belongs to a parent process)."""
        self._client = None

    def close(self) -> None:
        """Close the HTTP client, if one was opened."""
        if self._client is not None:
            self._client.close()

    def _http_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(transport=self._transport, timeout=self._timeout)
        return self._client

    def _send_batch(
        self, target: _Target, batch: list[SpanPayload], client: Mapping[str, Any] | None
    ) -> None:
        if not batch:
            return
        envelope: dict[str, Any] = {target.body_key: batch}
        if client is not None and target.body_key == SPANS:
            envelope["client"] = dict(client)
        body = json.dumps(envelope, separators=(",", ":"), default=str).encode()
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
                    self._split_and_send(target, batch, client)
                    return
                if not _is_retryable(status):
                    self.failed += len(batch)
                    self._log_rejected_batch(target, response, len(batch))
                    return
                failure = f"HTTP {status}"
                retry_after = parse_retry_after(response.headers.get("Retry-After"))

            if attempt >= self._max_retries or self._abort_retries.is_set():
                self.failed += len(batch)
                logger.warning(
                    "Dropping %d %s after %d attempts (%s)",
                    len(batch),
                    target.body_key,
                    attempt + 1,
                    failure,
                )
                return
            delay = self.retry_delay(attempt, retry_after)
            logger.debug("Export failed (%s); retrying in %.2fs", failure, delay)
            self._sleep(delay)

    def _split_and_send(
        self, target: _Target, batch: list[SpanPayload], client: Mapping[str, Any] | None
    ) -> None:
        """Retry an oversized (HTTP 413) batch as two smaller ones."""
        middle = len(batch) // 2
        self._send_batch(target, batch[:middle], client)
        self._send_batch(target, batch[middle:], client)

    def _log_rejected_batch(self, target: _Target, response: httpx.Response, size: int) -> None:
        if target.body_key == SCORES and response.status_code == 404:
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
            self.sent += batch_size
            return
        rejected = result.get("rejected") if isinstance(result, dict) else None
        if not isinstance(rejected, list) or not rejected:
            self.sent += batch_size
            return

        self.sent += batch_size - len(rejected)
        self.failed += len(rejected)
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

    def retry_delay(self, attempt: int, retry_after: float | None) -> float:
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
        seconds = float(value)
    except ValueError:
        pass
    else:
        # `nan` and `inf` parse as floats but are not delays; fall back to the backoff.
        return max(seconds, 0.0) if math.isfinite(seconds) else None
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
