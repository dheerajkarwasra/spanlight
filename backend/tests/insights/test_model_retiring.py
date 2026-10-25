"""`model_retiring` warns on calls to a model with an announced retirement date."""

import uuid
from datetime import UTC, datetime, timedelta

from app.insights.catalogue import render
from app.insights.context import Baselines, DetectorContext, GatewayAttrs, LlmSpanRow
from app.insights.detectors.model_retiring import detector
from app.insights.schemas import Severity, Window

# 30 days before `claude-sonnet-4-5-20250929` retires (2026-11-30).
NOW = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)


def _span(
    model: str, *, provider: str = "anthropic", trace: str = "a", fault: str | None = None
) -> LlmSpanRow:
    return LlmSpanRow(
        trace_id=trace * 32,
        span_id=trace * 16,
        session_id=None,
        external_user_id=None,
        environment=None,
        release=None,
        started_at=NOW - timedelta(hours=1),
        duration_ms=900,
        status="ok",
        error_class=None,
        provider=provider,
        model=model,
        input_tokens=10,
        output_tokens=5,
        cached_tokens=None,
        cost_usd=None,
        request_hash=None,
        finish_reason="stop",
        status_message=None,
        gateway=GatewayAttrs(route="support-bot", fault_scenario=fault),
        trace_name="answer_ticket",
    )


def test_a_retiring_snapshot_opens_one_finding_and_nothing_else_does() -> None:
    spans = [
        _span("claude-sonnet-4-5-20250929"),
        # A fault-injected call is not traffic.
        _span("claude-sonnet-4-5-20250929", trace="b", fault="provider_5xx"),
        # A `-latest` alias floats to a newer snapshot.
        _span("gpt-5-latest", provider="openai", trace="c"),
        # Active, with only a "Not sooner than" date.
        _span("claude-opus-4-5-20251101", trace="d"),
    ]
    ctx = DetectorContext(
        project_id=uuid.uuid4(),
        org_id=uuid.uuid4(),
        now=NOW,
        window=Window(start=NOW - timedelta(hours=24), end=NOW),
        llm_spans=spans,
        tool_spans=[],
        baselines=Baselines(),
        org_provider_errors=[],
    )

    findings = detector.run(ctx)

    assert [f.fingerprint_key for f in findings] == ["anthropic:claude-sonnet-4-5-20250929"]
    finding = findings[0]
    assert finding.severity == Severity.WARNING
    assert finding.evidence.trace_ids == ["a" * 32]
    assert finding.evidence.metrics == {"calls": 1, "days_left": 30}
    summary = render(finding).summary
    for named in ("support-bot", "answer_ticket", "2026-11-30", "claude-sonnet-5-5"):
        assert named in summary
