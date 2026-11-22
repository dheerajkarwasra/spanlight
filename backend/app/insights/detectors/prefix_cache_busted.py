"""`prefix_cache_busted`: a multi-step agent trace loses its prompt cache mid-conversation.

Reads: `ctx.llm_spans` of the last 24 hours (gateway cache hits excluded).
Rule: within one trace (calls to the same cache-capable model, ordered by start time), an
earlier call had `cached_tokens >= 1000` (proving caching is active), and a later call with at
least as many `input_tokens` drops to `cached_tokens == 0` (or < 10% of peak prior cache). This
is the classic signature of mutating the system prompt or tool order mid-loop.
Fires per model at >= 2 affected traces, critical at >= 10 traces. Key: model.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.insights import format as fmt
from app.insights.context import DetectorContext, LlmSpanRow
from app.insights.detectors._evidence import by_key, recent_trace_ids
from app.insights.detectors.cache_opportunity import CACHE_CAPABLE_PREFIXES
from app.insights.schemas import Evidence, Finding, Severity

KIND = "prefix_cache_busted"
WINDOW = timedelta(hours=24)
MIN_SAMPLES = 2

MIN_CACHE_TOKENS = 1_000
MIN_BUSTED_TRACES = 2
CRITICAL_BUSTED_TRACES = 10


def _trace_busted(calls: list[LlmSpanRow]) -> tuple[datetime, int] | None:
    """Return `(seen_at, lost_cached_tokens)` if prompt cache broke mid-trace."""
    ordered = sorted(calls, key=lambda s: (s.started_at, s.span_id))
    peak_cached = 0
    peak_input = 0
    lost_tokens = 0
    busted_at: datetime | None = None
    for call in ordered:
        inp = call.input_tokens or 0
        cached = call.cached_tokens or 0
        if cached >= MIN_CACHE_TOKENS:
            if cached > peak_cached:
                peak_cached = cached
                peak_input = inp
        elif peak_cached >= MIN_CACHE_TOKENS and inp >= peak_input and cached * 10 < peak_cached:
            lost_tokens += peak_cached - cached
            busted_at = call.started_at
    return (busted_at, lost_tokens) if busted_at is not None else None


@dataclass(frozen=True, slots=True)
class PrefixCacheBustedDetector:
    kind: str = KIND
    window: timedelta = WINDOW
    min_samples: int = MIN_SAMPLES

    def run(self, ctx: DetectorContext) -> list[Finding]:
        by_model_trace: dict[tuple[str, str], list[LlmSpanRow]] = defaultdict(list)
        for span in ctx.llm_spans:
            if (
                span.model is not None
                and not span.is_cache_hit
                and span.model.lower().startswith(CACHE_CAPABLE_PREFIXES)
                and span.input_tokens is not None
            ):
                by_model_trace[(span.model, span.trace_id)].append(span)

        per_model: dict[str, list[tuple[datetime, str, int]]] = defaultdict(list)
        for (model, trace_id), calls in by_model_trace.items():
            if len(calls) < 3:
                continue
            busted = _trace_busted(calls)
            if busted is not None:
                per_model[model].append((busted[0], trace_id, busted[1]))

        findings: list[Finding] = []
        for model, events in per_model.items():
            if len(events) < MIN_BUSTED_TRACES:
                continue
            lost = sum(item[2] for item in events)
            severity = (
                Severity.CRITICAL if len(events) >= CRITICAL_BUSTED_TRACES else Severity.WARNING
            )
            findings.append(
                Finding(
                    kind=KIND,
                    severity=severity,
                    fingerprint_key=model,
                    evidence=Evidence(
                        trace_ids=recent_trace_ids((ts, tid) for ts, tid, _ in events),
                        metrics={"traces": len(events), "lost_cached_tokens": lost},
                        window=ctx.window,
                    ),
                    params={
                        "model": model,
                        "traces": fmt.count_noun(len(events), "trace"),
                        "lost_cached_tokens": fmt.count(lost),
                    },
                )
            )
        return by_key(findings)


detector = PrefixCacheBustedDetector()
