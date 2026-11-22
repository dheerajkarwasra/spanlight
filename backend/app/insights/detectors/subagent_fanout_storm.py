"""`subagent_fanout_storm`: a single trace fans out into excessive child LLM/agent calls.

Reads: `ctx.llm_spans` of the last hour.
Rule: within one trace (or shared `parent_span_id` inside a trace), at least `MIN_CHILDREN`
(15) LLM calls originate inside one trace within the hour. Grouped by environment.
Severity: warning at >= 15 calls per trace; critical at >= 40 calls per trace.
Key: environment (`-` when unset). Metrics: `traces`, `max_calls`.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.insights import format as fmt
from app.insights.context import DetectorContext, LlmSpanRow
from app.insights.detectors._evidence import by_key, key_part, recent_trace_ids
from app.insights.schemas import Evidence, Finding, Severity

KIND = "subagent_fanout_storm"
WINDOW = timedelta(hours=1)
MIN_SAMPLES = 1

MIN_CHILDREN = 15
CRITICAL_CHILDREN = 40


@dataclass(frozen=True, slots=True)
class SubagentFanoutStormDetector:
    kind: str = KIND
    window: timedelta = WINDOW
    min_samples: int = MIN_SAMPLES

    def run(self, ctx: DetectorContext) -> list[Finding]:
        by_trace: dict[str, list[LlmSpanRow]] = defaultdict(list)
        for span in ctx.llm_spans:
            if not span.is_cache_hit and span.fault_scenario is None:
                by_trace[span.trace_id].append(span)

        per_env: dict[str | None, list[tuple[datetime, str, int]]] = defaultdict(list)
        for trace_id, calls in by_trace.items():
            # Count either whole-trace fan-out or single-parent fan-out.
            max_under_parent = 0
            parent_counts: dict[str, int] = defaultdict(int)
            for c in calls:
                if c.parent_span_id is not None:
                    parent_counts[c.parent_span_id] += 1
                    max_under_parent = max(max_under_parent, parent_counts[c.parent_span_id])
            fanout = max(len(calls), max_under_parent)
            if fanout < MIN_CHILDREN:
                continue
            latest = max(calls, key=lambda c: c.started_at)
            per_env[latest.environment].append((latest.started_at, trace_id, fanout))

        findings: list[Finding] = []
        for env, storms in per_env.items():
            max_calls = max(item[2] for item in storms)
            severity = Severity.CRITICAL if max_calls >= CRITICAL_CHILDREN else Severity.WARNING
            findings.append(
                Finding(
                    kind=KIND,
                    severity=severity,
                    fingerprint_key=key_part(env),
                    evidence=Evidence(
                        trace_ids=recent_trace_ids((ts, tid) for ts, tid, _ in storms),
                        metrics={"traces": len(storms), "max_calls": max_calls},
                        window=ctx.window,
                    ),
                    params={
                        "traces": fmt.count_noun(len(storms), "trace"),
                        "max_calls": fmt.count(max_calls),
                    },
                )
            )
        return by_key(findings)


detector = SubagentFanoutStormDetector()
