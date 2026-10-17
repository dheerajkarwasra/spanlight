"""The synthetic span builders produce spans the ingest schema accepts, and the spend-cap spans
classify as the product classifies a real Anthropic spend-cap refusal."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from accuracy.doctor.spans import llm_span, spread, tool_span
from accuracy.doctor.traffic_rate_limit import spend_cap_spans
from app.ingest.normalize import normalize_span
from app.ingest.schemas import SpanIn
from app.pricing.cost import PriceBook

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)


def _spans() -> list[dict[str, Any]]:
    started = NOW - timedelta(hours=30)
    return [
        llm_span(
            started=started,
            duration_ms=420,
            model="claude-haiku-5-5",
            environment="lab-x-pos",
            input_tokens=900,
            output_tokens=60,
            session_id="s-1",
            user_id="u-1",
        ),
        llm_span(
            started=started,
            duration_ms=120,
            model="claude-haiku-5-5",
            environment="lab-x-pos",
            input_tokens=300,
            output_tokens=0,
            status="error",
            status_message="429 rate_limit_error: slow down",
            http_status=429,
            finish_reason=None,
        ),
        llm_span(
            started=started,
            duration_ms=500,
            model="acme-llm-7b",
            provider="acme",
            environment=None,
            input_tokens=None,
            output_tokens=None,
            usage=False,
        ),
        llm_span(
            started=started,
            duration_ms=300,
            model="mistral-large-latest",
            provider="mistral",
            environment="lab-pi",
            input_tokens=10,
            output_tokens=0,
            status="error",
            status_message="503 api_error: Service unavailable",
            attributes={
                "spanlight.gateway.route": "doctor",
                "spanlight.gateway.upstream_status": 503,
                "spanlight.fault.scenario": "provider_5xx",
            },
        ),
        tool_span(
            started=started,
            duration_ms=30,
            name="search_docs",
            tool_input={"query": "refund"},
            trace_id="a" * 32,
            environment="lab-x-pos",
        ),
    ]


def _normalized(span: dict[str, Any], now: datetime = NOW) -> Any:
    parsed = SpanIn.model_validate(span)
    return normalize_span(parsed, capture_payloads=True, prices=PriceBook([]), now=now)


@pytest.mark.parametrize("index", range(5))
def test_builders_match_the_ingest_schema(index: int) -> None:
    span = _spans()[index]
    SpanIn.model_validate(span)
    assert len(span["trace_id"]) == 32
    assert len(span["span_id"]) == 16
    assert span["end_time"] >= span["start_time"]


def test_backdated_spans_stay_inside_retention() -> None:
    """Baseline spans are 26 to 40 hours old, far inside the 90-day ingest limit."""
    old = llm_span(
        started=datetime.now(tz=UTC) - timedelta(hours=40),
        duration_ms=1,
        model="m",
        environment="e",
        input_tokens=1,
        output_tokens=1,
    )
    parsed = SpanIn.model_validate(old)
    assert parsed.start_time > datetime.now(tz=UTC) - timedelta(days=90)


def test_error_span_has_no_output_and_keeps_its_status_code() -> None:
    span = _spans()[1]
    assert "output" not in span
    assert "finish_reason" not in span
    assert span["attributes"]["http.status_code"] == 429


def test_spread_is_even_and_oldest_first() -> None:
    times = spread(NOW - timedelta(hours=2), NOW, 4)
    assert times == [
        NOW - timedelta(hours=2),
        NOW - timedelta(minutes=90),
        NOW - timedelta(hours=1),
        NOW - timedelta(minutes=30),
    ]
    assert spread(NOW, NOW, 0) == []


def _spend_cap_batch() -> list[dict[str, Any]]:
    return spend_cap_spans(
        seed="rate_limit_pressure:spend_cap",
        model="claude-haiku-5-5",
        environment="lab-rate_limit_pressure-spend_cap",
        now=NOW,
        calls=60,
        limited=12,
    )


def test_spend_cap_batch_has_the_planted_share_inside_the_15_minute_window() -> None:
    spans = _spend_cap_batch()
    failed = [s for s in spans if s["status"] == "error"]
    assert len(spans) == 60
    assert len(failed) == 12
    assert all(NOW - timedelta(minutes=15) < datetime.fromisoformat(s["start_time"]) for s in spans)
    assert all(datetime.fromisoformat(s["end_time"]) < NOW for s in spans)


def test_spend_cap_spans_carry_the_anthropic_spend_cap_body() -> None:
    failed = [s for s in _spend_cap_batch() if s["status"] == "error"]
    for span in failed:
        assert span["provider"] == "anthropic"
        assert span["attributes"]["http.status_code"] == 429
        assert span["attributes"]["provider.error.type"] == "rate_limit_error"
        assert span["attributes"]["provider.error.code"] == "enforced_spend_limit_reached"
        assert "retry-after" not in str(span["attributes"]).lower()


def test_spend_cap_spans_classify_as_spend_cap_through_normalize_span() -> None:
    """The kind column the evidence shows reads `spend_cap`, by the ingest classifier."""
    spans = _spend_cap_batch()
    normalized = [_normalized(span) for span in spans]
    failed = [n for n in normalized if n.status == "error"]
    assert {n.error_class for n in failed} == {"rate_limit"}
    assert {n.rate_limit_kind for n in failed} == {"spend_cap"}
    assert {n.provider_error_code for n in failed} == {"enforced_spend_limit_reached"}
    assert {n.rate_limit_kind for n in normalized if n.status == "ok"} == {None}
