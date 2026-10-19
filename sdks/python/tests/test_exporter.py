"""Batching, retry, backoff and drop behaviour of the background exporter."""

from __future__ import annotations

import email.utils
import gzip
import json
import logging
import re
import threading
import time
from collections.abc import Callable, Iterator

import httpx
import pytest
from support import FakeIngestApi, decode_body

from spanlight._exporter import BatchExporter, parse_retry_after
from spanlight._version import __version__


def make_span(index: int) -> dict[str, object]:
    return {"span_id": f"{index:016x}", "name": f"span-{index}"}


class RecordingSleep:
    """Stands in for ``time.sleep`` so retry tests run instantly."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


@pytest.fixture
def sleeps() -> RecordingSleep:
    return RecordingSleep()


@pytest.fixture
def make_exporter(
    ingest: FakeIngestApi, sleeps: RecordingSleep
) -> Iterator[Callable[..., BatchExporter]]:
    created: list[BatchExporter] = []

    def factory(**overrides: object) -> BatchExporter:
        options: dict[str, object] = {
            "url": "http://spanlight.test/v1/traces",
            "api_key": "spl_live_abc_def",
            "batch_size": 100,
            "flush_interval": 60.0,
            "transport": ingest.transport(),
            "sleep": sleeps,
            "jitter": lambda: 0.5,
        }
        options.update(overrides)
        exporter = BatchExporter(**options)  # type: ignore[arg-type]
        created.append(exporter)
        return exporter

    yield factory
    for exporter in created:
        exporter.shutdown(timeout=1)


def wait_until(condition: Callable[[], bool], timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        time.sleep(0.01)


def status_sequence(*statuses: int, headers: dict[str, str] | None = None):
    """Respond with the given statuses in order, then 200 forever."""
    remaining = list(statuses)

    def handler(request: httpx.Request) -> httpx.Response:
        spans = decode_body(request)["spans"]
        if remaining:
            return httpx.Response(remaining.pop(0), headers=headers, json={"detail": "nope"})
        return httpx.Response(200, json={"accepted": len(spans), "rejected": []})

    return handler


# ---------------------------------------------------------------------- #
# Batching
# ---------------------------------------------------------------------- #


def test_flush_sends_batches_of_at_most_batch_size(make_exporter, ingest):
    exporter = make_exporter(batch_size=3)
    for index in range(7):
        exporter.export(make_span(index))

    assert exporter.flush(timeout=5)

    assert [len(batch) for batch in ingest.batches] == [3, 3, 1]
    sent_names = [span["name"] for span in ingest.spans]
    assert sent_names == [f"span-{index}" for index in range(7)]
    assert exporter.sent_count == 7


def test_full_batch_is_sent_without_waiting_for_interval(make_exporter, ingest):
    exporter = make_exporter(batch_size=2, flush_interval=60.0)
    exporter.export(make_span(1))
    exporter.export(make_span(2))

    wait_until(lambda: len(ingest.requests) == 1)


def test_partial_batch_is_sent_after_flush_interval(make_exporter, ingest):
    exporter = make_exporter(batch_size=100, flush_interval=0.1)
    exporter.export(make_span(1))

    wait_until(lambda: len(ingest.requests) == 1)
    assert ingest.spans[0]["name"] == "span-1"


def test_request_carries_bearer_key_and_json_body(make_exporter, ingest):
    exporter = make_exporter()
    exporter.export(make_span(1))
    exporter.flush(timeout=5)

    request = ingest.requests[0]
    assert request.method == "POST"
    assert str(request.url) == "http://spanlight.test/v1/traces"
    assert request.headers["Authorization"] == "Bearer spl_live_abc_def"
    assert request.headers["Content-Type"] == "application/json"
    assert request.headers["User-Agent"].startswith("spanlight-python/")
    assert request.headers["Content-Encoding"] == "gzip"  # on by default
    body = json.loads(gzip.decompress(request.content))
    assert body == {
        "spans": [make_span(1)],
        "client": {
            "sdk": "spanlight-python",
            "version": __version__,
            "exporter_id": body["client"]["exporter_id"],
            "dropped_total": 0,
            "failed_total": 0,
        },
    }
    assert re.fullmatch(r"[0-9a-f]{32}", body["client"]["exporter_id"])


def test_gzip_can_be_turned_off(make_exporter, ingest):
    exporter = make_exporter(gzip_body=False)
    exporter.export(make_span(1))
    exporter.flush(timeout=5)

    request = ingest.requests[0]
    assert "Content-Encoding" not in request.headers
    assert json.loads(request.content)["spans"] == [make_span(1)]


# ---------------------------------------------------------------------- #
# Bounded queue
# ---------------------------------------------------------------------- #


def test_full_queue_drops_oldest_spans_and_counts_them(make_exporter, ingest, caplog):
    exporter = make_exporter(max_queue=5, batch_size=100)
    with caplog.at_level(logging.WARNING, logger="spanlight"):
        for index in range(8):
            exporter.export(make_span(index))

    assert exporter.dropped_count == 3
    assert "queue is full" in caplog.text

    exporter.flush(timeout=5)
    assert [span["name"] for span in ingest.spans] == [f"span-{index}" for index in range(3, 8)]


# ---------------------------------------------------------------------- #
# Retries
# ---------------------------------------------------------------------- #


def test_retries_5xx_with_jittered_exponential_backoff(make_exporter, ingest, sleeps):
    ingest.handler = status_sequence(503, 502, 500)
    exporter = make_exporter(base_backoff=0.5)
    exporter.export(make_span(1))

    assert exporter.flush(timeout=5)

    assert len(ingest.requests) == 4
    # Equal jitter with jitter()=0.5: step/2 + 0.5 * step/2 for steps 0.5, 1, 2.
    assert sleeps.delays == pytest.approx([0.375, 0.75, 1.5])
    assert exporter.sent_count == 1
    assert exporter.failed_count == 0


def test_backoff_is_capped_at_max_backoff(make_exporter, ingest, sleeps):
    ingest.handler = status_sequence(500, 500, 500, 500)
    exporter = make_exporter(base_backoff=1.0, max_backoff=2.0, max_retries=5)
    exporter.export(make_span(1))
    exporter.flush(timeout=5)

    assert max(sleeps.delays) <= 2.0


def test_429_honours_retry_after_seconds(make_exporter, ingest, sleeps):
    ingest.handler = status_sequence(429, headers={"Retry-After": "7"})
    exporter = make_exporter()
    exporter.export(make_span(1))
    exporter.flush(timeout=5)

    assert len(ingest.requests) == 2
    assert sleeps.delays == [7.0]


def test_retry_after_is_capped_by_max_backoff(make_exporter, ingest, sleeps):
    ingest.handler = status_sequence(503, headers={"Retry-After": "3600"})
    exporter = make_exporter(max_backoff=30.0)
    exporter.export(make_span(1))
    exporter.flush(timeout=5)

    assert sleeps.delays == [30.0]


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_client_errors_are_not_retried_and_are_logged(
    make_exporter, ingest, sleeps, caplog, status
):
    ingest.handler = status_sequence(status)
    exporter = make_exporter()
    with caplog.at_level(logging.ERROR, logger="spanlight"):
        exporter.export(make_span(1))
        exporter.flush(timeout=5)

    assert len(ingest.requests) == 1
    assert sleeps.delays == []
    assert exporter.failed_count == 1
    assert f"HTTP {status}" in caplog.text


def test_gives_up_after_max_retries(make_exporter, ingest, sleeps, caplog):
    ingest.handler = lambda request: httpx.Response(500)
    exporter = make_exporter(max_retries=2)
    with caplog.at_level(logging.WARNING, logger="spanlight"):
        exporter.export(make_span(1))
        exporter.flush(timeout=5)

    assert len(ingest.requests) == 3
    assert len(sleeps.delays) == 2
    assert exporter.failed_count == 1
    assert "Dropping 1 spans after 3 attempts" in caplog.text


def test_network_errors_are_retried(make_exporter, ingest, sleeps):
    failures = [httpx.ConnectError("connection refused")]

    def handler(request: httpx.Request) -> httpx.Response:
        if failures:
            raise failures.pop()
        return httpx.Response(200, json={"accepted": 1, "rejected": []})

    ingest.handler = handler
    exporter = make_exporter()
    exporter.export(make_span(1))
    exporter.flush(timeout=5)

    assert len(ingest.requests) == 2
    assert len(sleeps.delays) == 1
    assert exporter.sent_count == 1


def test_payload_too_large_is_split_in_half(make_exporter, ingest):
    def handler(request: httpx.Request) -> httpx.Response:
        spans = decode_body(request)["spans"]
        if len(spans) > 2:
            return httpx.Response(413)
        return httpx.Response(200, json={"accepted": len(spans), "rejected": []})

    ingest.handler = handler
    exporter = make_exporter()
    for index in range(4):
        exporter.export(make_span(index))
    exporter.flush(timeout=5)

    accepted_sizes = [len(batch) for batch in ingest.batches if len(batch) <= 2]
    assert accepted_sizes == [2, 2]
    assert exporter.sent_count == 4


def test_rejected_spans_are_logged_with_reasons(make_exporter, ingest, caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "accepted": 1,
                "rejected": [{"index": 1, "span_id": "0000000000000001", "reason": "bad trace_id"}],
            },
        )

    ingest.handler = handler
    exporter = make_exporter()
    with caplog.at_level(logging.WARNING, logger="spanlight"):
        exporter.export(make_span(0))
        exporter.export(make_span(1))
        exporter.flush(timeout=5)

    assert "rejected 1 of 2 spans" in caplog.text
    assert "bad trace_id" in caplog.text
    assert exporter.sent_count == 1
    assert exporter.failed_count == 1


# ---------------------------------------------------------------------- #
# Robustness and lifecycle
# ---------------------------------------------------------------------- #


def test_unexpected_transport_failure_never_escapes(make_exporter, ingest):
    def handler(request: httpx.Request) -> httpx.Response:
        raise RuntimeError("transport bug")

    ingest.handler = handler
    exporter = make_exporter(max_retries=0)
    exporter.export(make_span(1))

    assert exporter.flush(timeout=5)
    assert exporter.failed_count == 1

    # The worker survives and keeps delivering later spans.
    ingest.handler = lambda request: httpx.Response(200, json={"accepted": 1, "rejected": []})
    exporter.export(make_span(2))
    assert exporter.flush(timeout=5)
    assert exporter.sent_count == 1


def test_flush_returns_false_when_the_timeout_expires(make_exporter, ingest):
    release = threading.Event()

    def slow_handler(request: httpx.Request) -> httpx.Response:
        release.wait(5)
        return httpx.Response(200, json={"accepted": 1, "rejected": []})

    ingest.handler = slow_handler
    exporter = make_exporter()
    exporter.export(make_span(1))

    started = time.monotonic()
    assert exporter.flush(timeout=0.2) is False
    assert time.monotonic() - started < 2

    release.set()
    assert exporter.flush(timeout=5)


def test_export_does_not_block_on_slow_network(make_exporter, ingest):
    release = threading.Event()

    def slow_handler(request: httpx.Request) -> httpx.Response:
        release.wait(5)
        return httpx.Response(200, json={"accepted": 1, "rejected": []})

    ingest.handler = slow_handler
    exporter = make_exporter(batch_size=1)

    started = time.monotonic()
    for index in range(50):
        exporter.export(make_span(index))
    elapsed = time.monotonic() - started
    release.set()

    assert elapsed < 0.5


def test_shutdown_flushes_and_ignores_later_spans(make_exporter, ingest):
    exporter = make_exporter()
    exporter.export(make_span(1))
    exporter.shutdown(timeout=5)
    exporter.export(make_span(2))
    exporter.shutdown(timeout=5)

    assert [span["name"] for span in ingest.spans] == ["span-1"]


def test_shutdown_aborts_pending_retries(ingest):
    ingest.handler = lambda request: httpx.Response(503)
    exporter = BatchExporter(
        url="http://spanlight.test/v1/traces",
        api_key="key",
        transport=ingest.transport(),
        base_backoff=10.0,
        max_retries=5,
    )
    exporter.export(make_span(1))

    started = time.monotonic()
    exporter.shutdown(timeout=0.5)

    assert time.monotonic() - started < 2
    wait_until(lambda: exporter.failed_count == 1)


def test_parse_retry_after_supports_seconds_and_http_dates():
    assert parse_retry_after(None) is None
    assert parse_retry_after("12") == 12.0
    assert parse_retry_after("-3") == 0.0
    assert parse_retry_after("not a date") is None

    future = email.utils.formatdate(time.time() + 30, usegmt=True)
    parsed = parse_retry_after(future)
    assert parsed is not None
    assert 25 <= parsed <= 31
