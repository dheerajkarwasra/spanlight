"""`agent_ping_pong_loop`: an agent alternating between two distinct tools without converging.

Reads: `ctx.tool_spans` of the last hour.
Rule: within one trace ordered by `(started_at, span_id)`, find maximal alternating cycles
`A, B, A, B, ...` where `A != B` (using `(name, input_hash)` pairs). An alternating sequence
qualifies when the pair `(A, B)` repeats at least 3 full cycles (`>= 6` consecutive spans).
Severity: warning; critical when the longest sequence reaches 6 cycles (`>= 12` spans).
Key: `name_a <-> name_b`. Metrics: `traces`, `max_cycles`.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.insights import format as fmt
from app.insights.context import DetectorContext, ToolSpanRow
from app.insights.detectors._evidence import by_key, recent_trace_ids
from app.insights.schemas import Evidence, Finding, Severity

KIND = "agent_ping_pong_loop"
WINDOW = timedelta(hours=1)
MIN_SAMPLES = 1

MIN_CYCLES = 3
CRITICAL_CYCLES = 6


def _ping_pong_in_trace(spans: list[ToolSpanRow]) -> list[tuple[str, int, datetime]]:
    """Return `(cycle_key, cycle_count, last_started_at)` for alternating tool loops."""
    ordered = sorted(spans, key=lambda s: (s.started_at, s.span_id))
    results: list[tuple[str, int, datetime]] = []
    i = 0
    while i + 3 < len(ordered):
        first, second = ordered[i], ordered[i + 1]
        if first.name == second.name:
            i += 1
            continue
        pair_a = (first.name, first.input_hash)
        pair_b = (second.name, second.input_hash)
        length = 2
        while i + length < len(ordered):
            expected = pair_a if length % 2 == 0 else pair_b
            candidate = ordered[i + length]
            if (candidate.name, candidate.input_hash) != expected:
                break
            length += 1
        cycles = length // 2
        if cycles >= MIN_CYCLES:
            label_pair = " <-> ".join(sorted((first.name, second.name)))
            results.append((label_pair, cycles, ordered[i + length - 1].started_at))
            i += length
        else:
            i += 1
    return results


@dataclass(frozen=True, slots=True)
class AgentPingPongLoopDetector:
    kind: str = KIND
    window: timedelta = WINDOW
    min_samples: int = MIN_SAMPLES

    def run(self, ctx: DetectorContext) -> list[Finding]:
        by_trace: dict[str, list[ToolSpanRow]] = defaultdict(list)
        for span in ctx.tool_spans:
            by_trace[span.trace_id].append(span)
        grouped: dict[str, list[tuple[str, int, datetime]]] = defaultdict(list)
        for trace_id, spans in by_trace.items():
            for pair_key, cycles, seen in _ping_pong_in_trace(spans):
                grouped[pair_key].append((trace_id, cycles, seen))
        findings: list[Finding] = []
        for pair_key, entries in grouped.items():
            max_cycles = max(cycles for _, cycles, _ in entries)
            traces = {trace_id for trace_id, _, _ in entries}
            severity = Severity.CRITICAL if max_cycles >= CRITICAL_CYCLES else Severity.WARNING
            findings.append(
                Finding(
                    kind=KIND,
                    severity=severity,
                    fingerprint_key=pair_key,
                    evidence=Evidence(
                        trace_ids=recent_trace_ids((seen, tid) for tid, _, seen in entries),
                        metrics={"traces": len(traces), "max_cycles": max_cycles},
                        window=ctx.window,
                    ),
                    params={
                        "pair": pair_key,
                        "traces": fmt.count_noun(len(traces), "trace"),
                        "max_cycles": fmt.count(max_cycles),
                    },
                )
            )
        return by_key(findings)


detector = AgentPingPongLoopDetector()
