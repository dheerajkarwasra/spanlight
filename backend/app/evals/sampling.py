"""Which recent traces online evaluation picks, and which span in them it evaluates. Pure.

Sampling is deterministic: a trace is in an evaluator's sample when the first 8 hex digits of
`sha256("<evaluator_id>:<trace_id>")`, read as a fraction of 2^32, are below the sampling rate.
The same trace gets the same answer on every rescan and on every worker, so the sampler's
overlapping windows never change their minds, and raising the rate only adds traces.

Traces Spanlight makes itself are never sampled: those in the environments of evaluations, the
playground, experiments and the Doctor, and those tagged `spanlight:judge`. Without this, a judge
would sample its own traces and run forever.

`app.evals.sampler_queries` applies the same rules in SQL to keep the candidate list short; these
functions are the definition, and the sampler checks every candidate against them again.
"""

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.evals.config import EvaluatorFilters

EXCLUDED_ENVIRONMENTS = frozenset({"evals", "playground", "experiments", "doctor"})
JUDGE_TAG = "spanlight:judge"
HASH_HEX_DIGITS = 8
HASH_SPACE = 0x1_0000_0000  # 2^32: every value of `HASH_HEX_DIGITS` hex digits


@dataclass(frozen=True, slots=True)
class TraceFacts:
    """What the filters look at. `models` are the models of the trace's `llm` spans."""

    trace_id: str
    name: str | None
    environment: str | None
    tags: tuple[str, ...]
    duration_ms: float | None
    models: frozenset[str]


@dataclass(frozen=True, slots=True)
class SpanFacts:
    """An `llm` span of a trace, without its payload; `has_payload` says one is stored."""

    span_id: str
    name: str
    model: str | None
    started_at: datetime
    has_payload: bool


@dataclass(frozen=True, slots=True)
class TraceWithSpans:
    trace: TraceFacts
    llm_spans: tuple[SpanFacts, ...]


@dataclass(frozen=True, slots=True)
class EvalTarget:
    """The span an evaluation reads. `has_payload` false means it cannot be evaluated."""

    trace_id: str
    span_id: str
    has_payload: bool


def sample_point(evaluator_id: uuid.UUID, trace_id: str) -> int:
    """The trace's position in the evaluator's sample, in `[0, 2^32)`."""
    digest = hashlib.sha256(f"{evaluator_id}:{trace_id}".encode()).hexdigest()
    return int(digest[:HASH_HEX_DIGITS], 16)


def sample_threshold(rate: Decimal) -> int:
    """The bound a `sample_point` must stay below: `point / 2^32 < rate` is `point < bound`
    for whole points. 0 for a rate of 0 (never), 2^32 for a rate of 1 (always)."""
    if rate <= 0:
        return 0
    if rate >= 1:
        return HASH_SPACE
    scaled = rate * HASH_SPACE
    whole = int(scaled)
    return whole if whole == scaled else whole + 1


def should_sample(evaluator_id: uuid.UUID, trace_id: str, rate: Decimal) -> bool:
    """Whether the evaluator samples the trace at `rate` (0 never, 1 always)."""
    return sample_point(evaluator_id, trace_id) < sample_threshold(rate)


def is_excluded(trace: TraceFacts) -> bool:
    """Whether the trace is one Spanlight made itself, which no evaluator samples."""
    return trace.environment in EXCLUDED_ENVIRONMENTS or JUDGE_TAG in trace.tags


def matches_filters(trace: TraceFacts, filters: EvaluatorFilters) -> bool:
    """Whether the trace passes every filter. An empty list matches everything; a non-empty one
    needs the trace's value in it (for `tags` and `models`, any one of the trace's values)."""
    if is_excluded(trace):
        return False
    if filters.environments and trace.environment not in filters.environments:
        return False
    if filters.trace_names and trace.name not in filters.trace_names:
        return False
    if filters.tags and not set(filters.tags) & set(trace.tags):
        return False
    if filters.models and not set(filters.models) & trace.models:
        return False
    minimum = filters.min_duration_ms
    return minimum is None or (trace.duration_ms is not None and trace.duration_ms >= minimum)


def select_target(trace: TraceWithSpans) -> EvalTarget | None:
    """The trace's last `llm` span by start time (ties by span id), or None without one."""
    if not trace.llm_spans:
        return None
    last = max(trace.llm_spans, key=lambda span: (span.started_at, span.span_id))
    return EvalTarget(trace.trace.trace_id, last.span_id, last.has_payload)


@dataclass(frozen=True, slots=True)
class Picks:
    """One evaluator's choice in one cycle: the targets to queue and what was left out."""

    targets: tuple[EvalTarget, ...]
    skipped_cap: int
    skipped_no_payload: int


def pick_targets(
    evaluator_id: uuid.UUID,
    rate: Decimal,
    filters: EvaluatorFilters,
    candidates: list[TraceWithSpans],
    cap: int,
) -> Picks:
    """The sampled, matching candidates' targets, at most `cap`. A target without a stored
    payload cannot be evaluated and is counted apart; one past the cap waits for a later cycle
    (the windows overlap) or ages out."""
    targets: list[EvalTarget] = []
    over_cap = no_payload = 0
    for candidate in candidates:
        trace = candidate.trace
        if not should_sample(evaluator_id, trace.trace_id, rate):
            continue
        if not matches_filters(trace, filters):
            continue
        target = select_target(candidate)
        if target is None:
            continue
        if not target.has_payload:
            no_payload += 1
        elif len(targets) >= cap:
            over_cap += 1
        else:
            targets.append(target)
    return Picks(tuple(targets), over_cap, no_payload)
