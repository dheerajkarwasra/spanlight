"""`context_growth`: conversations whose input grows call after call without bound.

Reads: `ctx.llm_spans` of the last 24 hours. Fault-injected spans are used (this judges the
client's message history, not provider health).

Rule: a session (non-null `session_id`) qualifies when it has at least 8 llm calls with a known
`input_tokens`, ordered by `started_at`, `input_tokens` rises (next > previous) on at least 80 %
of consecutive pairs, and the last call is at least 3x the first and at least 20 000 tokens.
Qualifying sessions are grouped by the environment of their last call. A group fires on at
least 3 qualifying sessions, or on 1 whose last call reaches 100 000 tokens.

Severity: warning; critical when any qualifying session's last call reaches 150 000 tokens.
Key: environment (`-` when unset). Metrics: `sessions`, `max_last_input_tokens`.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta
from itertools import pairwise

from app.insights import format as fmt
from app.insights.context import DetectorContext, LlmSpanRow
from app.insights.detectors._evidence import by_key, key_part, span_trace_ids
from app.insights.schemas import Evidence, Finding, Severity

KIND = "context_growth"
WINDOW = timedelta(hours=24)
MIN_SAMPLES = 1  # one qualifying session

MIN_CALLS = 8
RISING_PAIRS_NUM, RISING_PAIRS_DEN = 4, 5  # rises on at least 4/5 (80 %) of the pairs
MIN_GROWTH_FACTOR = 3
MIN_LAST_INPUT_TOKENS = 20_000
MIN_SESSIONS = 3
LONE_SESSION_LAST_INPUT_TOKENS = 100_000
CRITICAL_LAST_INPUT_TOKENS = 150_000


@dataclass(frozen=True, slots=True)
class _GrowingSession:
    environment: str | None
    last_input_tokens: int
    calls: tuple[LlmSpanRow, ...]


def _qualifying_session(calls: list[LlmSpanRow]) -> _GrowingSession | None:
    """The session if its calls (known input tokens, any order) show unbounded growth."""
    ordered = sorted(calls, key=lambda c: (c.started_at, c.span_id))
    if len(ordered) < MIN_CALLS:
        return None
    tokens = [c.input_tokens for c in ordered if c.input_tokens is not None]
    pairs = len(tokens) - 1
    rises = sum(1 for prev, nxt in pairwise(tokens) if nxt > prev)
    first, last = tokens[0], tokens[-1]
    if rises * RISING_PAIRS_DEN < pairs * RISING_PAIRS_NUM:
        return None
    if last < first * MIN_GROWTH_FACTOR or last < MIN_LAST_INPUT_TOKENS:
        return None
    return _GrowingSession(ordered[-1].environment, last, tuple(ordered))


def _growing_sessions(ctx: DetectorContext) -> list[_GrowingSession]:
    by_flow: dict[str, list[LlmSpanRow]] = defaultdict(list)
    for span in ctx.llm_spans:
        if span.input_tokens is not None:
            flow_key = span.session_id if span.session_id is not None else f"trace:{span.trace_id}"
            by_flow[flow_key].append(span)
    found = (_qualifying_session(calls) for calls in by_flow.values())
    return [session for session in found if session is not None]


def _finding(
    environment: str | None, sessions: list[_GrowingSession], ctx: DetectorContext
) -> Finding | None:
    max_last = max(s.last_input_tokens for s in sessions)
    if len(sessions) < MIN_SESSIONS and max_last < LONE_SESSION_LAST_INPUT_TOKENS:
        return None
    severity = Severity.CRITICAL if max_last >= CRITICAL_LAST_INPUT_TOKENS else Severity.WARNING
    return Finding(
        kind=KIND,
        severity=severity,
        fingerprint_key=key_part(environment),
        evidence=Evidence(
            trace_ids=span_trace_ids(call for s in sessions for call in s.calls),
            metrics={"sessions": len(sessions), "max_last_input_tokens": max_last},
            window=ctx.window,
        ),
        params={
            "sessions": fmt.count_noun(len(sessions), "session"),
            "max_last_input_tokens": fmt.count(max_last),
        },
    )


@dataclass(frozen=True, slots=True)
class ContextGrowthDetector:
    kind: str = KIND
    window: timedelta = WINDOW
    min_samples: int = MIN_SAMPLES

    def run(self, ctx: DetectorContext) -> list[Finding]:
        by_environment: dict[str | None, list[_GrowingSession]] = defaultdict(list)
        for session in _growing_sessions(ctx):
            by_environment[session.environment].append(session)
        findings = (_finding(env, sessions, ctx) for env, sessions in by_environment.items())
        return by_key(f for f in findings if f is not None)


detector = ContextGrowthDetector()
