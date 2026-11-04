"""`error_spike` on a slice with no 7-day baseline: an absolute 25 % rule over the last 24 hours,
warning only, so a new or small project still hears about a failing model."""

import dataclasses
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.insights.catalogue import render
from app.insights.context import ANY, Baselines, DetectorContext, LlmSpanRow
from app.insights.detectors.error_spike import MIN_CALLS, detector
from app.insights.digest import insights_section
from app.insights.digest_queries import OpenInsightRow
from app.insights.schemas import Severity, Window

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
ENV, MODEL = "production", "gpt-4.1-mini"


def _span(i: int, *, error: bool, age: timedelta) -> LlmSpanRow:
    return LlmSpanRow(
        trace_id=f"{i:032x}",
        span_id=f"{i:016x}",
        session_id=None,
        external_user_id=None,
        environment=ENV,
        release=None,
        started_at=NOW - age,
        duration_ms=900,
        status="error" if error else "ok",
        error_class="provider_5xx" if error else None,
        provider="openai",
        model=MODEL,
        input_tokens=10,
        output_tokens=5,
        cached_tokens=None,
        cost_usd=None,
        request_hash=None,
        finish_reason=None,
        status_message=None,
    )


def _spans(calls: int, errors: int, age: timedelta = timedelta(hours=6)) -> list[LlmSpanRow]:
    return [_span(i, error=i < errors, age=age) for i in range(calls)]


def _ctx(spans: list[LlmSpanRow], baselines: Baselines | None = None) -> DetectorContext:
    """The context as the engine slices it for the detector's window."""
    ctx = DetectorContext(
        project_id=uuid.uuid4(),
        org_id=uuid.uuid4(),
        now=NOW,
        window=Window(start=NOW - timedelta(hours=24), end=NOW),
        llm_spans=spans,
        tool_spans=[],
        baselines=baselines or Baselines(),
        org_provider_errors=[],
    )
    return ctx.sliced(Window(start=NOW - detector.window, end=NOW))


def test_no_baseline_and_30_percent_errors_over_24_hours_opens_a_warning() -> None:
    [finding] = detector.run(_ctx(_spans(MIN_CALLS, errors=15)))

    assert finding.severity == Severity.WARNING  # critical needs a baseline
    assert finding.fingerprint_key == f"{ENV}:{MODEL}"
    assert finding.evidence.metrics == {
        "error_rate": Decimal("0.3000"),
        "baseline_error_rate": None,
        "calls": MIN_CALLS,
        "errors": 15,
    }
    assert finding.evidence.window == Window(start=NOW - timedelta(hours=24), end=NOW)
    copy = render(finding)
    # Nothing was compared, so nothing "jumped".
    assert copy.title == f"Error rate is 30.0 % on {MODEL} in {ENV}"
    assert "No baseline yet" in copy.summary
    assert "last 24 hours" in copy.summary
    assert "—" not in copy.summary and "None" not in copy.summary
    digest = insights_section(
        {Severity.WARNING: 1}, [OpenInsightRow(finding.kind, finding.severity, copy.title)]
    )
    assert digest.paragraphs[1] == f"Error spike: Error rate is 30.0 % on {MODEL} in {ENV}"


def test_no_baseline_below_25_percent_is_silent() -> None:
    assert detector.run(_ctx(_spans(100, errors=24))) == []


def test_no_baseline_below_min_samples_is_silent() -> None:
    assert detector.run(_ctx(_spans(MIN_CALLS - 1, errors=MIN_CALLS - 1))) == []


def test_a_baselined_slice_keeps_the_15_minute_rule() -> None:
    baselines = Baselines({("error_rate", ENV, ANY, MODEL): Decimal("0.02")})
    # 30 % errors hours ago, outside the 15-minute spike window: no finding with a baseline.
    assert detector.run(_ctx(_spans(MIN_CALLS, errors=15), baselines)) == []

    recent = _spans(MIN_CALLS, errors=15, age=timedelta(minutes=5))
    [finding] = detector.run(_ctx(recent, baselines))

    assert finding.severity == Severity.CRITICAL
    assert finding.fingerprint_key == f"{ENV}:{MODEL}"
    assert finding.evidence.metrics["baseline_error_rate"] == Decimal("0.0200")
    assert finding.evidence.window == Window(start=NOW - timedelta(minutes=15), end=NOW)
    copy = render(finding)
    assert copy.title.startswith("Error rate jumped to ")
    assert copy.summary.endswith(" over the 7 days before the last 24 hours.")


def test_slices_with_and_without_a_baseline_are_judged_side_by_side() -> None:
    baselines = Baselines({("error_rate", "staging", ANY, MODEL): Decimal("0.02")})
    old = _spans(MIN_CALLS, errors=15)  # production, no baseline: the 24-hour rule
    recent = [
        dataclasses.replace(span, environment="staging", trace_id=f"{i + 500:032x}")
        for i, span in enumerate(_spans(MIN_CALLS, errors=15, age=timedelta(minutes=5)))
    ]

    findings = detector.run(_ctx(old + recent, baselines))

    assert [(f.fingerprint_key, f.severity) for f in findings] == [
        (f"production:{MODEL}", Severity.WARNING),
        (f"staging:{MODEL}", Severity.CRITICAL),
    ]
