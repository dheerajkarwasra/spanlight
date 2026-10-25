"""``inject`` and ``continue_trace``: W3C trace context across processes."""

from __future__ import annotations

import contextvars

from support import flushed_spans

import spanlight


def test_continue_trace_parents_spans_on_the_injected_span(tracer, ingest):
    with tracer.span("caller") as caller:
        meta = spanlight.inject({"progressToken": 1})

    assert meta == {
        "progressToken": 1,
        "traceparent": f"00-{caller.trace_id}-{caller.span_id}-01",
    }

    def serve(carrier: dict[str, object]) -> None:
        with spanlight.continue_trace(carrier), tracer.span("handle-tool"):
            pass

    # A fresh context stands in for the server process.
    contextvars.Context().run(serve, meta)
    contextvars.Context().run(serve, {"traceparent": f"00-{'0' * 32}-{caller.span_id}-01"})

    spans = flushed_spans(tracer, ingest)
    assert len(spans) == 3  # the placeholder parent is never exported
    child, restarted = [span for span in spans if span["name"] == "handle-tool"]
    assert child["trace_id"] == caller.trace_id
    assert child["parent_span_id"] == caller.span_id
    assert child["trace"]["name"] is None
    assert child["trace"]["environment"] is None
    # An all-zero trace id is invalid: the span starts a new trace of its own.
    assert restarted["trace_id"] != caller.trace_id
    assert restarted["parent_span_id"] is None
