"""Unit tests for Doctor 2.0 fixes and new agentic detectors."""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.insights.catalogue import render
from app.insights.context import Baselines, DetectorContext, LlmSpanRow, ToolSpanRow
from app.insights.detectors import (
    agent_ping_pong_loop,
    cache_opportunity,
    context_growth,
    prefix_cache_busted,
    retry_after_ignored,
    subagent_fanout_storm,
    tool_error_ignored,
    tool_schema_hallucination,
    truncated_stream_accepted,
)
from app.insights.schemas import Window

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)


def _ctx(
    *, llm_spans: list[LlmSpanRow] | None = None, tool_spans: list[ToolSpanRow] | None = None
) -> DetectorContext:
    return DetectorContext(
        project_id=uuid.uuid4(),
        org_id=uuid.uuid4(),
        now=NOW,
        window=Window(start=NOW - timedelta(hours=24), end=NOW),
        llm_spans=llm_spans or [],
        tool_spans=tool_spans or [],
        baselines=Baselines(),
        org_provider_errors=[],
    )


def _llm(
    idx: int,
    *,
    trace_id: str = "t1",
    session_id: str | None = None,
    model: str = "gpt-5",
    input_tokens: int = 1000,
    cached_tokens: int | None = None,
    status: str = "ok",
    error_class: str | None = None,
    request_hash: str | None = "a" * 32,
    provider_retry_after_s: Decimal | None = None,
    unreported_reason: str | None = None,
    parent_span_id: str | None = None,
) -> LlmSpanRow:
    return LlmSpanRow(
        trace_id=trace_id,
        span_id=f"{idx:016x}",
        session_id=session_id,
        external_user_id=None,
        environment="prod",
        release=None,
        started_at=NOW - timedelta(minutes=30) + timedelta(seconds=idx * 5),
        duration_ms=500,
        status=status,
        error_class=error_class,
        provider="openai",
        model=model,
        input_tokens=input_tokens,
        output_tokens=100,
        cached_tokens=cached_tokens,
        cost_usd=Decimal("0.01"),
        request_hash=request_hash,
        finish_reason="stop",
        status_message=None,
        parent_span_id=parent_span_id,
        provider_retry_after_s=provider_retry_after_s,
        unreported_reason=unreported_reason,
    )


def _tool(
    idx: int,
    name: str,
    *,
    trace_id: str = "t1",
    status: str = "ok",
    status_message: str | None = None,
    input_hash: str | None = "h1",
) -> ToolSpanRow:
    return ToolSpanRow(
        trace_id=trace_id,
        span_id=f"f{idx:015x}",
        session_id=None,
        started_at=NOW - timedelta(minutes=25) + timedelta(seconds=idx * 5),
        name=name,
        input_hash=input_hash,
        status=status,
        status_message=status_message,
    )


def test_context_growth_detects_intra_trace_growth_without_session_id() -> None:
    spans = [_llm(i, session_id=None, input_tokens=5_000 + i * 15_000) for i in range(8)]
    findings = context_growth.detector.run(_ctx(llm_spans=spans))
    assert len(findings) == 1
    assert findings[0].evidence.metrics["max_last_input_tokens"] >= 100_000


def test_cache_opportunity_includes_gemini_models() -> None:
    spans = [
        _llm(i, trace_id=f"t{i % 3}", model="gemini-2.5-pro", input_tokens=4000) for i in range(10)
    ]
    findings = cache_opportunity.detector.run(_ctx(llm_spans=spans))
    assert len(findings) == 1 and findings[0].fingerprint_key == "gemini-2.5-pro"


def test_retry_after_ignored_uses_sdk_provider_retry_after_attribute() -> None:
    spans: list[LlmSpanRow] = []
    for i in range(3):
        tid = f"tr{i}"
        first = _llm(
            i * 10,
            trace_id=tid,
            status="error",
            error_class="rate_limit",
            provider_retry_after_s=Decimal("10.0"),
        )
        retry = _llm(i * 10 + 1, trace_id=tid, status="ok")
        spans.extend((first, retry))
    findings = retry_after_ignored.detector.run(_ctx(llm_spans=spans))
    assert len(findings) == 1 and findings[0].evidence.metrics["pairs"] == 3


def test_truncated_stream_accepted_detects_real_stream_cuts() -> None:
    cut = _llm(1, trace_id="cut1", request_hash="1" * 32, unreported_reason="stream_error")
    next_call = _llm(2, trace_id="cut1", request_hash="2" * 32)
    findings = truncated_stream_accepted.detector.run(_ctx(llm_spans=[cut, next_call]))
    assert len(findings) == 1 and findings[0].evidence.metrics["rounds"] == 1


def test_agent_ping_pong_loop_detects_alternating_cycles() -> None:
    tools = [
        _tool(i, "planner" if i % 2 == 0 else "verifier", input_hash="a" if i % 2 == 0 else "b")
        for i in range(6)
    ]
    findings = agent_ping_pong_loop.detector.run(_ctx(tool_spans=tools))
    assert len(findings) == 1 and findings[0].evidence.metrics["max_cycles"] == 3
    assert "planner" in render(findings[0]).title


def test_tool_error_ignored_detects_unretried_tool_failures() -> None:
    tools = [_tool(0, "charge_card", status="error", trace_id=f"t{i}") for i in range(2)]
    llms = [_llm(120, trace_id=f"t{i}", status="ok") for i in range(2)]
    findings = tool_error_ignored.detector.run(_ctx(llm_spans=llms, tool_spans=tools))
    assert len(findings) == 1 and findings[0].evidence.metrics["ignored_calls"] == 2
    assert "charge_card" in render(findings[0]).title


def test_tool_schema_hallucination_detects_validation_failures() -> None:
    tools = [
        _tool(
            i,
            "create_issue",
            status="error",
            status_message="ValidationError: missing required 'repo'",
        )
        for i in range(3)
    ]
    findings = tool_schema_hallucination.detector.run(_ctx(tool_spans=tools))
    assert len(findings) == 1 and findings[0].evidence.metrics["errors"] == 3
    assert "create_issue" in render(findings[0]).title


def test_prefix_cache_busted_detects_mid_trace_cache_collapse() -> None:
    spans: list[LlmSpanRow] = []
    for tid in ("t1", "t2"):
        spans.extend(
            [
                _llm(
                    1,
                    trace_id=tid,
                    model="claude-sonnet-5-5",
                    input_tokens=3000,
                    cached_tokens=2000,
                ),
                _llm(
                    2,
                    trace_id=tid,
                    model="claude-sonnet-5-5",
                    input_tokens=3500,
                    cached_tokens=2500,
                ),
                _llm(
                    3, trace_id=tid, model="claude-sonnet-5-5", input_tokens=4000, cached_tokens=0
                ),
            ]
        )
    findings = prefix_cache_busted.detector.run(_ctx(llm_spans=spans))
    assert len(findings) == 1 and findings[0].evidence.metrics["traces"] == 2
    assert "claude-sonnet-5-5" in render(findings[0]).title


def test_subagent_fanout_storm_detects_excessive_llm_fanout() -> None:
    spans = [_llm(i, trace_id="storm1", parent_span_id="root_1") for i in range(16)]
    findings = subagent_fanout_storm.detector.run(_ctx(llm_spans=spans))
    assert len(findings) == 1 and findings[0].evidence.metrics["max_calls"] == 16
    assert "16" in render(findings[0]).title
