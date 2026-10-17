"""Trace metadata: ``update_trace(metadata=)``, its lenient validation and its merge rule.

The validation and merge cases come from ``sdks/fixtures/metadata-cases.json``, the table the
server's ``app/ingest/metadata.py`` is tested against too, so the two cannot drift.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest
from support import flushed_spans

import spanlight
from spanlight._metadata import clean_metadata
from spanlight._trace_state import TraceState

_CASES = json.loads(
    (Path(__file__).resolve().parents[2] / "fixtures" / "metadata-cases.json").read_text("utf-8")
)


def test_update_trace_metadata_accumulates_and_replaces_per_key(tracer, ingest):
    with tracer.span("request"):
        spanlight.update_trace(metadata={"customer_id": "acme", "feature": "search"})
        spanlight.update_trace(metadata={"customer_id": "globex", "tier": 3})

    metadata = flushed_spans(tracer, ingest)[0]["trace"]["metadata"]
    assert metadata == {"customer_id": "globex", "feature": "search", "tier": "3"}


def test_client_update_trace_passes_metadata(tracer, ingest):
    with tracer.span("request"):
        tracer.update_trace(metadata={"team": "ml"})

    assert flushed_spans(tracer, ingest)[0]["trace"]["metadata"] == {"team": "ml"}


def test_invalid_metadata_is_dropped_with_a_debug_log_never_raised(tracer, ingest, caplog):
    caplog.set_level(logging.DEBUG, logger="spanlight")
    with tracer.span("request"):
        spanlight.update_trace(
            metadata={
                "customer_id": "acme",
                "Bad-Key": "secret-one",
                "nested": {"secret": "secret-two"},
                "empty": "",
                "ratio": float("nan"),
                "gone": None,
            }
        )
        spanlight.update_trace(metadata=["not", "a", "mapping"])  # type: ignore[arg-type]

    assert flushed_spans(tracer, ingest)[0]["trace"]["metadata"] == {"customer_id": "acme"}
    logged = "\n".join(record.getMessage() for record in caplog.records)
    for key in ("Bad-Key", "nested", "empty", "ratio"):
        assert repr(key) in logged
    assert "secret" not in logged
    assert "gone" not in logged  # a null value drops its key silently


def test_payload_omits_an_empty_metadata_object(tracer, ingest):
    with tracer.span("request"):
        spanlight.update_trace(metadata={"only": None})

    assert "metadata" not in flushed_spans(tracer, ingest)[0]["trace"]


def test_outside_a_span_is_a_noop():
    spanlight.update_trace(metadata={"customer_id": "acme"})  # must not raise


@pytest.mark.parametrize("case", _CASES["clean"], ids=lambda case: case["name"])
def test_fixture_lenient_cases(case: dict[str, Any]) -> None:
    assert clean_metadata(case["raw"]) == case["lenient"]


@pytest.mark.parametrize("case", _CASES["merge"], ids=lambda case: case["name"])
def test_fixture_merge_cases(case: dict[str, Any]) -> None:
    trace = TraceState()
    trace.metadata = dict(case["current"])
    trace.update(metadata=case["incoming"])
    assert trace.metadata == case["expected"]
