"""Delivery trust boundaries exercised through the real HTTP transport."""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from spanlight._exporter import BatchExporter


@contextmanager
def ingestion(replies):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            requests.append(json.loads(body))
            reply = replies[min(len(requests) - 1, len(replies) - 1)]
            status, headers, reply = reply if isinstance(reply, tuple) else (200, {}, reply)
            self.send_response(status)
            for key, value in headers.items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(reply)))
            self.end_headers()
            self.wfile.write(reply)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1/traces", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


VALID = b'{"accepted":2,"rejected":[]}'
INVALID = [
    b"<html>secret credential</html>",
    b'{"accepted":',
    b'{"accepted":1,"rejected":[]}',
    b"{}",
    b"null",
    b'{"accepted":true,"rejected":[{"index":0}]}',
    b'{"accepted":1.0,"rejected":[{"index":0}]}',
    b'{"accepted":0,"rejected":[{"index":0},{"index":0}]}',
    b'{"accepted":1,"rejected":[{"index":2}]}',
    b'{"accepted":1,"rejected":[{"index":true}]}',
    b'{"accepted":1,"rejected":[{"index":-1}]}',
    b'{"accepted":1,"rejected":[null]}',
    b'{"accepted":1,"rejected":[{"index":0.5}]}',
    b'{"accepted":-1,"rejected":[]}',
    b'{"accepted":2,"rejected":0}',
]


@pytest.mark.parametrize("reply", INVALID)
def test_ambiguous_ack_retries_identical_ids_then_confirms(reply):
    with ingestion([reply, VALID]) as (url, requests):
        exporter = BatchExporter(url=url, api_key="k", gzip_body=False, sleep=lambda _: None)
        try:
            exporter.export({"span_id": "a"})
            exporter.export({"span_id": "b"})
            assert exporter.flush(timeout=3)
            assert exporter.sent_count == 2
            assert exporter.failed_count == 0
            assert len(requests) == 2
            assert requests[0] == requests[1]
        finally:
            exporter.shutdown(timeout=1)


def test_ambiguous_ack_exhaustion_counts_loss_without_response_secrets(caplog):
    with ingestion([INVALID[0]]) as (url, requests):
        exporter = BatchExporter(
            url=url, api_key="k", gzip_body=False, max_retries=1, sleep=lambda _: None
        )
        try:
            exporter.export({"span_id": "a"})
            assert exporter.flush(timeout=3)
            assert exporter.sent_count == 0
            assert exporter.failed_count == 1
            assert len(requests) == 2
            assert "ambiguous acknowledgment" in caplog.text
            assert "secret credential" not in caplog.text
        finally:
            exporter.shutdown(timeout=1)


@pytest.mark.parametrize("kind", ["spans", "scores"])
def test_supported_partial_ack_preserves_counter_conservation(kind):
    from spanlight._exporter_delivery import Delivery

    with ingestion([b'{"accepted":1,"rejected":[{"index":1,"reason":"invalid"}]}']) as (
        url,
        requests,
    ):
        delivery = Delivery(
            url=url,
            api_key="k",
            scores_url=url,
            gzip_body=False,
            timeout=1,
            max_retries=1,
            transport=None,
            sleep=None,
            jitter=lambda: 0,
            base_backoff=0.001,
            max_backoff=30,
        )
        try:
            delivery.send(kind, [{"span_id": "a", "id": "a"}, {"span_id": "b", "id": "b"}])
            assert delivery.sent == 1
            assert delivery.failed == 1
            assert len(requests) == 1
        finally:
            delivery.close()


@pytest.mark.parametrize("header", ["120", "1e20", "9" * 400])
def test_shutdown_interrupts_long_server_minimum_without_retry(header):
    import time

    with ingestion([(503, {"Retry-After": header}, b"")]) as (url, requests):
        exporter = BatchExporter(url=url, api_key="k", gzip_body=False)
        exporter.export({"span_id": "a"})
        thread = threading.Thread(target=lambda: exporter.flush(timeout=2))
        thread.start()
        deadline = time.monotonic() + 1
        while not requests and time.monotonic() < deadline:
            time.sleep(0.001)
        assert len(requests) == 1
        time.sleep(0.02)
        started = time.monotonic()
        exporter.shutdown(timeout=0.05)
        thread.join(timeout=1)
        assert not thread.is_alive()
        assert time.monotonic() - started < 0.5
        assert len(requests) == 1
        assert exporter.sent_count == 0
        assert exporter.failed_count == 1


def test_all_partial_ack_subsets_conserve_counts_and_preserve_order():
    from itertools import combinations

    from spanlight._exporter_delivery import Delivery

    # Exhaust every rejection subset for batches of one through five entries.
    cases = [
        (size, subset)
        for size in range(1, 6)
        for count in range(size + 1)
        for subset in combinations(range(size), count)
    ]
    replies = [
        json.dumps(
            {
                "accepted": size - len(subset),
                "rejected": [{"index": index} for index in reversed(subset)],
            }
        ).encode()
        for size, subset in cases
    ]
    with ingestion(replies) as (url, requests):
        delivery = Delivery(
            url=url,
            api_key="k",
            scores_url=url,
            gzip_body=False,
            timeout=1,
            max_retries=1,
            transport=None,
            sleep=None,
            jitter=lambda: 0,
            base_backoff=0.001,
            max_backoff=30,
        )
        try:
            for size, subset in cases:
                previous_sent, previous_failed = delivery.sent, delivery.failed
                delivery.send("spans", [{"span_id": str(index)} for index in range(size)])
                assert delivery.sent - previous_sent == size - len(subset)
                assert delivery.failed - previous_failed == len(subset)
                assert requests[-1]["spans"] == [{"span_id": str(index)} for index in range(size)]
            assert len(requests) == len(cases)
        finally:
            delivery.close()


@pytest.mark.parametrize("dated", [False, True])
def test_server_minimum_exceeds_backoff_cap_over_real_http(dated):
    import email.utils
    import time

    header = email.utils.formatdate(time.time() + 122, usegmt=True) if dated else "120"
    delays = []
    with ingestion([(503, {"Retry-After": header}, b""), VALID]) as (url, requests):
        exporter = BatchExporter(url=url, api_key="k", gzip_body=False, sleep=delays.append)
        try:
            exporter.export({"span_id": "a"})
            exporter.export({"span_id": "b"})
            assert exporter.flush(timeout=2)
            assert len(requests) == 2
            assert len(delays) == 1
            assert delays[0] >= 120
            assert exporter.sent_count == 2
            assert exporter.failed_count == 0
        finally:
            exporter.shutdown(timeout=1)


def test_older_server_score_404_remains_permanent_and_logged_once(caplog):
    from spanlight._exporter_delivery import Delivery

    with ingestion([(404, {}, b"missing")]) as (url, requests):
        delivery = Delivery(
            url=url,
            api_key="k",
            scores_url=url,
            gzip_body=False,
            timeout=1,
            max_retries=3,
            transport=None,
            sleep=None,
            jitter=lambda: 0,
            base_backoff=0.001,
            max_backoff=30,
        )
        try:
            delivery.send("scores", [{"id": "a"}])
            delivery.send("scores", [{"id": "b"}])
            assert delivery.sent == 0
            assert delivery.failed == 2
            assert len(requests) == 2
            assert caplog.text.count("does not accept scores") == 1
        finally:
            delivery.close()
