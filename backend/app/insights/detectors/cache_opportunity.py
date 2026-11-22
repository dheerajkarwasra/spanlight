"""`cache_opportunity`: long identical requests that are sent again and again without caching.

Reads: `ctx.llm_spans` of the last 24 hours. Gateway cache hits are excluded; fault-injected
spans are used (this judges the client's prompts).

Rule: a call qualifies when its model starts with a prompt-cache-capable prefix
(`CACHE_CAPABLE_PREFIXES`: a capability list, deliberately not the price matcher), it has a
`request_hash`, `input_tokens >= 2 000` and `cached_tokens` null or 0. A hash qualifies with at
least 10 such calls across at least 2 distinct traces. A model fires when it has a qualifying hash.

Severity: info. Key: model. Metrics: `repeated_hashes`, `repeated_calls`,
`estimated_input_tokens` (the input tokens of the repeated calls).
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta

from app.insights import format as fmt
from app.insights.context import DetectorContext, LlmSpanRow
from app.insights.detectors._evidence import by_key, span_trace_ids
from app.insights.schemas import Evidence, Finding, Severity

KIND = "cache_opportunity"
WINDOW = timedelta(hours=24)
MIN_SAMPLES = 1  # one qualifying hash

CACHE_CAPABLE_PREFIXES = (
    "claude-",
    "gpt-4o",
    "gpt-4.1",
    "gpt-5",
    "gpt-6",
    "gemini-",
    "o1",
    "o3",
    "o4",
)
MIN_INPUT_TOKENS = 2_000
MIN_REPEATS = 10
MIN_TRACES = 2


def _qualifies(span: LlmSpanRow) -> bool:
    return (
        span.model is not None
        and span.model.lower().startswith(CACHE_CAPABLE_PREFIXES)
        and span.request_hash is not None
        and span.input_tokens is not None
        and span.input_tokens >= MIN_INPUT_TOKENS
        and not span.cached_tokens
        and not span.is_cache_hit
    )


def _repeated_hashes(ctx: DetectorContext) -> dict[str, list[list[LlmSpanRow]]]:
    """Per model, the call groups (one per hash) that repeat enough to count."""
    groups: dict[tuple[str, str], list[LlmSpanRow]] = defaultdict(list)
    for span in ctx.llm_spans:
        if span.model is not None and span.request_hash is not None and _qualifies(span):
            groups[(span.model, span.request_hash)].append(span)
    per_model: dict[str, list[list[LlmSpanRow]]] = defaultdict(list)
    for (model, _), calls in groups.items():
        if len(calls) >= MIN_REPEATS and len({c.trace_id for c in calls}) >= MIN_TRACES:
            per_model[model].append(calls)
    return per_model


def _finding(model: str, groups: list[list[LlmSpanRow]], ctx: DetectorContext) -> Finding:
    calls = [call for group in groups for call in group]
    estimated = sum(call.input_tokens or 0 for call in calls)
    return Finding(
        kind=KIND,
        severity=Severity.INFO,
        fingerprint_key=model,
        evidence=Evidence(
            trace_ids=span_trace_ids(calls),
            metrics={
                "repeated_hashes": len(groups),
                "repeated_calls": len(calls),
                "estimated_input_tokens": estimated,
            },
            window=ctx.window,
        ),
        params={
            "model": model,
            "repeated_hashes": fmt.count_noun(len(groups), "request"),
            "repeated_calls": fmt.count(len(calls)),
            "estimated_input_tokens": fmt.count(estimated),
        },
    )


@dataclass(frozen=True, slots=True)
class CacheOpportunityDetector:
    kind: str = KIND
    window: timedelta = WINDOW
    min_samples: int = MIN_SAMPLES

    def run(self, ctx: DetectorContext) -> list[Finding]:
        per_model = _repeated_hashes(ctx)
        return by_key(_finding(model, groups, ctx) for model, groups in per_model.items())


detector = CacheOpportunityDetector()
