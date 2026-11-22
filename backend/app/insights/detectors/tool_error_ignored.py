"""`tool_error_ignored`: tool spans fail with an error, yet the trace continues without retrying.

Reads: `ctx.tool_spans` (last hour) and `ctx.llm_spans` (same window).
Rule: within a trace, a tool span with `status == "error"` qualifies as ignored when a later
`llm` span in the same trace succeeds (`status == "ok"`) without an intervening or subsequent
call to the same tool name in that trace. Fires per tool name at >= 2 traces (or >= 5 ignored
calls), critical at >= 10 ignored calls. Key: tool name. Metrics: `ignored_calls`, `traces`.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.insights import format as fmt
from app.insights.context import DetectorContext, LlmSpanRow, ToolSpanRow
from app.insights.detectors._evidence import by_key, recent_trace_ids
from app.insights.schemas import Evidence, Finding, Severity

KIND = "tool_error_ignored"
WINDOW = timedelta(hours=1)
MIN_SAMPLES = 2

MIN_TRACES = 2
MIN_CALLS_SINGLE_TRACE = 5
CRITICAL_CALLS = 10


def _ignored_in_trace(
    tools: list[ToolSpanRow], llms: list[LlmSpanRow]
) -> list[tuple[str, datetime]]:
    if not llms:
        return []
    ordered_tools = sorted(tools, key=lambda s: (s.started_at, s.span_id))
    ok_llms = [s.started_at for s in llms if s.status == "ok"]
    if not ok_llms:
        return []
    last_ok_llm = max(ok_llms)
    last_by_name: dict[str, datetime] = {}
    for span in ordered_tools:
        last_by_name[span.name] = span.started_at

    ignored: list[tuple[str, datetime]] = []
    for index, span in enumerate(ordered_tools):
        if span.status != "error":
            continue
        if span.started_at >= last_ok_llm:
            continue
        retried_same = any(later.name == span.name for later in ordered_tools[index + 1 :])
        if not retried_same and last_by_name.get(span.name, span.started_at) == span.started_at:
            ignored.append((span.name, span.started_at))
    return ignored


@dataclass(frozen=True, slots=True)
class ToolErrorIgnoredDetector:
    kind: str = KIND
    window: timedelta = WINDOW
    min_samples: int = MIN_SAMPLES

    def run(self, ctx: DetectorContext) -> list[Finding]:
        tools_by_trace: dict[str, list[ToolSpanRow]] = defaultdict(list)
        for span in ctx.tool_spans:
            tools_by_trace[span.trace_id].append(span)
        llms_by_trace: dict[str, list[LlmSpanRow]] = defaultdict(list)
        for llm in ctx.llm_spans:
            llms_by_trace[llm.trace_id].append(llm)

        grouped: dict[str, list[tuple[str, datetime]]] = defaultdict(list)
        for trace_id, tools in tools_by_trace.items():
            for tool_name, seen in _ignored_in_trace(tools, llms_by_trace.get(trace_id, [])):
                grouped[tool_name].append((trace_id, seen))

        findings: list[Finding] = []
        for tool_name, events in grouped.items():
            traces = {tid for tid, _ in events}
            if len(traces) < MIN_TRACES and len(events) < MIN_CALLS_SINGLE_TRACE:
                continue
            severity = Severity.CRITICAL if len(events) >= CRITICAL_CALLS else Severity.WARNING
            findings.append(
                Finding(
                    kind=KIND,
                    severity=severity,
                    fingerprint_key=tool_name,
                    evidence=Evidence(
                        trace_ids=recent_trace_ids((seen, tid) for tid, seen in events),
                        metrics={"ignored_calls": len(events), "traces": len(traces)},
                        window=ctx.window,
                    ),
                    params={
                        "name": tool_name,
                        "ignored_calls": fmt.count_noun(len(events), "call"),
                        "traces": fmt.count_noun(len(traces), "trace"),
                    },
                )
            )
        return by_key(findings)


detector = ToolErrorIgnoredDetector()
