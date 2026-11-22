"""Helpers shared by the stream and output detectors.

`truncated_stream_accepted` and `unsupported_parameter_retried` both look for a fault-injected
span that the client answered with a different request. Both judge client behaviour, so the
fault-injected spans are the evidence, not noise to drop. Both look within one trace: a trace is
one logical request (a lab round), and a new round in the same session after a failure is
correct client behaviour.
"""

from bisect import bisect_left, bisect_right
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from datetime import timedelta

from app.insights.context import DetectorContext, LlmSpanRow
from app.insights.detectors._evidence import by_key, key_part, span_trace_ids
from app.insights.format import count_noun
from app.insights.schemas import Evidence, Finding, Severity


def fault_rounds(
    spans: Iterable[LlmSpanRow],
    scenario: str,
    horizon: timedelta,
    *,
    forbid_resend: bool = False,
    predicate: Callable[[LlmSpanRow], bool] | None = None,
) -> list[LlmSpanRow]:
    """Fault spans of `scenario` (or matching `predicate`) answered with a different request.

    A round qualifies when a span of the same trace starts after the fault, no later than
    `horizon` after the fault's start, with a known request hash other than the fault's. With
    `forbid_resend`, it also needs no later span in the trace (at any time) with the fault's own
    hash. A fault without a request hash never qualifies.
    """
    matches = predicate or (lambda s: s.fault_scenario == scenario)
    by_trace: dict[str, list[LlmSpanRow]] = defaultdict(list)
    for span in spans:
        by_trace[span.trace_id].append(span)
    rounds: list[LlmSpanRow] = []
    for trace_spans in by_trace.values():
        if any(matches(s) for s in trace_spans):
            ordered = sorted(trace_spans, key=lambda s: s.started_at)
            rounds.extend(_trace_rounds(ordered, matches, horizon, forbid_resend))
    return rounds


def _trace_rounds(
    ordered: Sequence[LlmSpanRow],
    matches: Callable[[LlmSpanRow], bool],
    horizon: timedelta,
    forbid_resend: bool,
) -> list[LlmSpanRow]:
    """Rounds of one trace, in O(n log n): positions per hash and a prefix count of hashed spans."""
    times = [s.started_at for s in ordered]
    positions: dict[str, list[int]] = defaultdict(list)
    hashed_before = [0]
    for index, span in enumerate(ordered):
        if span.request_hash is not None:
            positions[span.request_hash].append(index)
        hashed_before.append(hashed_before[-1] + (span.request_hash is not None))
    rounds: list[LlmSpanRow] = []
    for fault in ordered:
        if not matches(fault) or fault.request_hash is None:
            continue
        start = bisect_right(times, fault.started_at)  # strictly after the fault
        stop = bisect_right(times, fault.started_at + horizon)
        same = positions[fault.request_hash]
        same_in_range = bisect_left(same, stop) - bisect_left(same, start)
        if hashed_before[stop] - hashed_before[start] - same_in_range <= 0:
            continue
        if forbid_resend and same[-1] >= start:
            continue
        rounds.append(fault)
    return rounds


def findings_per_environment(
    ctx: DetectorContext, kind: str, severity: Severity, rounds: Sequence[LlmSpanRow]
) -> list[Finding]:
    """One finding per environment that has rounds; metrics `rounds`, param `rounds`."""
    by_environment: dict[str | None, list[LlmSpanRow]] = defaultdict(list)
    for fault in rounds:
        by_environment[fault.environment].append(fault)
    findings = [
        Finding(
            kind=kind,
            severity=severity,
            fingerprint_key=key_part(environment),
            evidence=Evidence(
                trace_ids=span_trace_ids(faults),
                metrics={"rounds": len(faults)},
                window=ctx.window,
            ),
            params={"rounds": count_noun(len(faults), "lab round")},
        )
        for environment, faults in by_environment.items()
    ]
    return by_key(findings)
