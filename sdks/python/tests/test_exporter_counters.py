"""The exporter's cumulative counters in the ``client`` object of every spans batch."""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest
from support import FakeIngestApi, decode_body

from spanlight import _exporter
from spanlight._exporter import BatchExporter
from spanlight._version import __version__

_HEX_32 = re.compile(r"[0-9a-f]{32}")


def make_span(index: int) -> dict[str, object]:
    return {"span_id": f"{index:016x}", "name": f"span-{index}"}


def accept_any(request: httpx.Request) -> httpx.Response:
    body = decode_body(request)
    items = body.get("spans", body.get("scores", []))
    return httpx.Response(200, json={"accepted": len(items), "rejected": []})


@pytest.fixture
def make_exporter(ingest: FakeIngestApi) -> Iterator[Callable[..., BatchExporter]]:
    created: list[BatchExporter] = []
    ingest.handler = accept_any

    def factory(**overrides: object) -> BatchExporter:
        options: dict[str, object] = {
            "url": "http://spanlight.test/v1/traces",
            "api_key": "spl_live_abc_def",
            "batch_size": 100,
            "flush_interval": 60.0,
            "transport": ingest.transport(),
            "sleep": lambda _seconds: None,
            "jitter": lambda: 0.5,
            "max_retries": 0,
        }
        options.update(overrides)
        exporter = BatchExporter(**options)  # type: ignore[arg-type]
        created.append(exporter)
        return exporter

    yield factory
    for exporter in created:
        exporter.shutdown(timeout=1)


def bodies(ingest: FakeIngestApi) -> list[dict[str, Any]]:
    return [decode_body(request) for request in ingest.requests]


def test_spans_batch_carries_client_counters(make_exporter, ingest):
    exporter = make_exporter()
    exporter.export(make_span(1))
    assert exporter.flush(timeout=5)

    client = bodies(ingest)[0]["client"]
    assert set(client) == {"sdk", "version", "exporter_id", "dropped_total", "failed_total"}
    assert client["sdk"] == "spanlight-python"
    assert client["version"] == __version__
    assert _HEX_32.fullmatch(client["exporter_id"])
    assert client["dropped_total"] == 0
    assert client["failed_total"] == 0


def test_totals_are_cumulative_not_deltas(make_exporter, ingest):
    exporter = make_exporter(max_queue=2)
    for index in range(5):
        exporter.export(make_span(index))
    assert exporter.flush(timeout=5)
    # flush() can return before the worker clears its flush request; until it
    # does, the worker would ship the next span at once, with the old total.
    with exporter._condition:
        assert exporter._condition.wait_for(lambda: not exporter._flush_requested, timeout=5)
    for index in range(4):
        exporter.export(make_span(10 + index))
    assert exporter.flush(timeout=5)

    assert [body["client"]["dropped_total"] for body in bodies(ingest)] == [3, 5]


def test_failed_total_counts_undeliverable_batches(make_exporter, ingest):
    statuses = [400]

    def handler(request: httpx.Request) -> httpx.Response:
        if statuses:
            return httpx.Response(statuses.pop(0), json={"detail": "nope"})
        return accept_any(request)

    ingest.handler = handler
    exporter = make_exporter()
    exporter.export(make_span(1))
    exporter.export(make_span(2))
    assert exporter.flush(timeout=5)
    exporter.export(make_span(3))
    assert exporter.flush(timeout=5)

    assert [body["client"]["failed_total"] for body in bodies(ingest)] == [0, 2]


def test_scores_batch_has_no_client_object(make_exporter, ingest):
    exporter = make_exporter(scores_url="http://spanlight.test/v1/scores")
    exporter.export(make_span(1))
    exporter.export_score({"id": "score-1", "name": "accuracy", "value": 1.0})
    assert exporter.flush(timeout=5)

    spans_body, scores_body = bodies(ingest)
    assert "client" in spans_body
    assert scores_body == {"scores": [{"id": "score-1", "name": "accuracy", "value": 1.0}]}


def test_fork_regenerates_the_exporter_id(make_exporter, ingest, monkeypatch):
    exporter = make_exporter()
    parent_id = exporter.client_counters["exporter_id"]

    child_pid = os.getpid() + 1
    monkeypatch.setattr(_exporter.os, "getpid", lambda: child_pid)
    exporter.export(make_span(1))
    assert exporter.flush(timeout=5)

    child_id = bodies(ingest)[0]["client"]["exporter_id"]
    assert _HEX_32.fullmatch(child_id)
    assert child_id != parent_id


def test_fork_starts_the_child_counters_at_zero(make_exporter, ingest, monkeypatch):
    exporter = make_exporter(max_queue=1)
    # No worker thread in the "parent": a real fork leaves the parent's thread
    # behind, whereas here it would keep running against the child's state.
    monkeypatch.setattr(exporter, "_ensure_worker", lambda: None)
    exporter.export(make_span(1))
    exporter.export(make_span(2))
    assert exporter.client_counters["dropped_total"] == 1
    monkeypatch.delattr(exporter, "_ensure_worker")

    child_pid = os.getpid() + 1
    monkeypatch.setattr(_exporter.os, "getpid", lambda: child_pid)
    exporter.export(make_span(3))
    assert exporter.flush(timeout=5)

    client = bodies(ingest)[-1]["client"]
    assert client["dropped_total"] == 0
    assert client["failed_total"] == 0


def test_client_is_the_same_id_across_batches(make_exporter, ingest):
    exporter = make_exporter(batch_size=1)
    exporter.export(make_span(1))
    exporter.export(make_span(2))
    assert exporter.flush(timeout=5)

    ids = {body["client"]["exporter_id"] for body in bodies(ingest)}
    assert len(bodies(ingest)) == 2
    assert len(ids) == 1
