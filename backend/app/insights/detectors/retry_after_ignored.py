"""retry_after_ignored: the client retries before the provider's Retry-After delay is over.

Reads: llm spans of the last hour. Fault-injected spans are kept: the Integration Lab's
`rate_limited` scenario is exactly how this client behaviour is observed.

Rule: a pair is a span A with error class `rate_limit`, a `gateway.retry_after_s`, a request
hash and a known duration, followed by the first span B (by start time) with the same request
hash in the same trace or the same non-null session, that starts after A ended but less than
`retry_after_s` after it. A.ended_at is `started_at + duration_ms`. Each A pairs with at most
one B. Pairs are grouped by the environment of A; a group with at least `MIN_PAIRS` pairs
opens one warning. `median_early_by_s` is the median of `retry_after_s - (B.started_at -
A.ended_at)`, rounded to one place.
"""

from bisect import bisect_left
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from statistics import median

from app.insights import format as fmt
from app.insights.context import DetectorContext, LlmSpanRow
from app.insights.detectors._evidence import by_key, key_part, recent_trace_ids
from app.insights.schemas import Evidence, Finding, Severity

KIND = "retry_after_ignored"
WINDOW = timedelta(hours=1)
MIN_PAIRS = 3

_MICROSECONDS = Decimal(1_000_000)


@dataclass(frozen=True, slots=True)
class _Pair:
    first: LlmSpanRow
    retry: LlmSpanRow
    early_by_s: Decimal


def _seconds(delta: timedelta) -> Decimal:
    return Decimal(delta // timedelta(microseconds=1)) / _MICROSECONDS


def _same_flow(a: LlmSpanRow, b: LlmSpanRow) -> bool:
    return a.trace_id == b.trace_id or (a.session_id is not None and a.session_id == b.session_id)


def _delay_and_end(span: LlmSpanRow) -> tuple[Decimal, datetime] | None:
    """`(retry_after_s, ended_at)` of a rate-limited span that asked for a delay, else `None`."""
    if span.error_class != "rate_limit" or span.duration_ms is None:
        return None
    delay = (
        span.gateway.retry_after_s
        if span.gateway is not None and span.gateway.retry_after_s is not None
        else span.provider_retry_after_s
    )
    if delay is None:
        return None
    return delay, span.started_at + timedelta(milliseconds=span.duration_ms)


def _pair_for(
    first: LlmSpanRow,
    delay: Decimal,
    ended_at: datetime,
    candidates: list[LlmSpanRow],
    starts: list[datetime],
) -> _Pair | None:
    """The first retry of `first` that began before its Retry-After delay had passed."""
    for index in range(bisect_left(starts, ended_at), len(candidates)):
        retry = candidates[index]
        gap = _seconds(retry.started_at - ended_at)
        if gap >= delay:
            return None
        if retry.span_id != first.span_id and _same_flow(first, retry):
            return _Pair(first, retry, delay - gap)
    return None


def find_pairs(spans: list[LlmSpanRow]) -> list[_Pair]:
    by_hash: dict[str, list[LlmSpanRow]] = defaultdict(list)
    for span in sorted(spans, key=lambda s: (s.started_at, s.span_id)):
        if span.request_hash is not None:
            by_hash[span.request_hash].append(span)

    pairs: list[_Pair] = []
    for same_hash in by_hash.values():
        starts = [s.started_at for s in same_hash]
        for span in same_hash:
            asked = _delay_and_end(span)
            if asked is None:
                continue
            pair = _pair_for(span, asked[0], asked[1], same_hash, starts)
            if pair is not None:
                pairs.append(pair)
    return pairs


def _finding(environment: str | None, pairs: list[_Pair], ctx: DetectorContext) -> Finding:
    early = fmt.round_seconds(median(p.early_by_s for p in pairs))
    contributions = [(p.retry.started_at, p.retry.trace_id) for p in pairs]
    contributions += [(p.first.started_at, p.first.trace_id) for p in pairs]
    return Finding(
        kind=KIND,
        severity=Severity.WARNING,
        fingerprint_key=key_part(environment),
        evidence=Evidence(
            trace_ids=recent_trace_ids(contributions),
            metrics={"pairs": len(pairs), "median_early_by_s": early},
            window=ctx.window,
        ),
        params={
            "pairs": fmt.count(len(pairs)),
            "median_early_by": fmt.duration_ms(early * 1000),
        },
    )


@dataclass(frozen=True, slots=True)
class RetryAfterIgnoredDetector:
    kind: str = KIND
    window: timedelta = WINDOW
    min_samples: int = MIN_PAIRS

    def run(self, ctx: DetectorContext) -> list[Finding]:
        by_environment: dict[str | None, list[_Pair]] = defaultdict(list)
        for pair in find_pairs(list(ctx.llm_spans)):
            by_environment[pair.first.environment].append(pair)
        return by_key(
            _finding(environment, pairs, ctx)
            for environment, pairs in by_environment.items()
            if len(pairs) >= self.min_samples
        )


detector = RetryAfterIgnoredDetector()
