"""The export encoders carry every span column the trace detail shows.

Failure mode pinned here: a span column added to the read model (`usage_rollup`) that the JSONL
export silently drops, so a downloaded trace disagrees with the trace page about which spans
count toward the totals.
"""

import json
from datetime import UTC, datetime
from decimal import Decimal

from app.exports.schemas import SpanExportRow, TraceExportRow
from app.exports.writers import jsonl_line

AT = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)


def _span(*, usage_rollup: bool) -> SpanExportRow:
    return SpanExportRow(
        span_id="a" * 16,
        parent_span_id=None,
        kind="llm",
        name="call",
        status="ok",
        status_message=None,
        started_at=AT,
        ended_at=AT,
        duration_ms=1.0,
        provider="openai",
        model="gpt-4.1",
        input_tokens=10,
        output_tokens=5,
        cached_tokens=None,
        cache_write_tokens=None,
        cache_write_1h_tokens=None,
        cost_usd=Decimal("0.001"),
        pricing_version=None,
        time_to_first_token_ms=None,
        input=None,
        output=None,
        attributes={},
        truncated=False,
        usage_rollup=usage_rollup,
    )


def _trace(span: SpanExportRow) -> TraceExportRow:
    return TraceExportRow(
        trace_id="b" * 32,
        name="answer",
        started_at=AT,
        ended_at=AT,
        duration_ms=1.0,
        status="ok",
        environment=None,
        release=None,
        user_id=None,
        session_id=None,
        span_count=1,
        error_count=0,
        input_tokens=10,
        output_tokens=5,
        cost_usd=Decimal("0.001"),
        has_unpriced=False,
        tags=[],
        metadata={},
        spans=[span],
    )


def test_jsonl_span_carries_usage_rollup() -> None:
    [exported] = json.loads(jsonl_line(_trace(_span(usage_rollup=True))))["spans"]

    assert exported["usage_rollup"] is True
    assert exported["cost_usd"] == "0.001"
