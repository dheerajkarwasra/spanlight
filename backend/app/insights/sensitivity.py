"""Doctor sensitivity: a gate on one stored metric per check (UX Phase 6, spec section 4.1).

A gate runs after the detector's own rule, so High recall (no row, the default) is exactly the
detector's behaviour and the stricter settings only drop findings. The value gated on is one the
detector already writes into `Finding.evidence.metrics`. Five checks have no dial and stay fixed:
`provider_incident` (organization-wide), `unpriced_spend` (hiding it would make cost look
complete), `truncated_stream_accepted` and `unsupported_parameter_retried` (one round is the
whole signal), and `model_retiring` (one call to a model about to stop working is enough). A
finding whose metric is missing, `None` or unreadable always passes: nothing is hidden on missing
data.

Metrics arrive as `Decimal` and `int` from a detector, and as strings and ints from stored
evidence (decimals are stored as strings), so `strength` reads both.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Any, Literal

from app.insights.catalogue import KIND_INFO

Level = Literal["high_recall", "balanced", "high_precision"]
DEFAULT_LEVEL: Level = "high_recall"


@dataclass(frozen=True, slots=True)
class Gate:
    """The smallest reading a finding needs at each stricter level; `metric_label` names the
    reading in plain words for the settings screen."""

    metric_label: str
    balanced: Decimal
    high_precision: Decimal


GATES: Mapping[str, Gate] = MappingProxyType(
    {
        "error_spike": Gate("error rate", Decimal("0.10"), Decimal("0.25")),
        "rate_limit_pressure": Gate(
            "rate-limited share of calls", Decimal("0.20"), Decimal("0.30")
        ),
        "truncated_outputs": Gate("truncated share of calls", Decimal("0.20"), Decimal("0.30")),
        "cost_spike": Gate("spend vs baseline", Decimal(5), Decimal(10)),
        "latency_regression": Gate("p95 vs baseline", Decimal(2), Decimal(3)),
        "retry_storm": Gate("largest retry burst", Decimal(10), Decimal(20)),
        "tool_loop": Gate("repeats of one tool call", Decimal(6), Decimal(10)),
        "context_growth": Gate("last call input tokens", Decimal(50_000), Decimal(100_000)),
        "cache_opportunity": Gate("repeated calls", Decimal(25), Decimal(50)),
        "client_timeout_misconfigured": Gate("aborted calls", Decimal(25), Decimal(50)),
        "retry_after_ignored": Gate("early retries", Decimal(5), Decimal(10)),
    }
)
TUNABLE: frozenset[str] = frozenset(GATES)
ALL_DETECTORS: tuple[str, ...] = tuple(KIND_INFO)
"""Every check, in catalogue order (the order of `app.insights.engine.DETECTORS`)."""

# The evidence metric each gate reads; latency_regression is a ratio of two (see `strength`).
_METRIC: Mapping[str, str] = MappingProxyType(
    {
        "error_spike": "error_rate",
        "rate_limit_pressure": "share",
        "truncated_outputs": "share",
        "cost_spike": "ratio",
        "retry_storm": "largest_storm",
        "tool_loop": "max_repeats",
        "context_growth": "max_last_input_tokens",
        "cache_opportunity": "repeated_calls",
        "client_timeout_misconfigured": "aborts",
        "retry_after_ignored": "pairs",
    }
)


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def strength(kind: str, metrics: Mapping[str, Any]) -> Decimal | None:
    """The reading the kind's gate compares, or None for a fixed check or a missing value."""
    if kind == "latency_regression":
        p95 = _decimal(metrics.get("p95_ms"))
        baseline = _decimal(metrics.get("baseline_p95_ms"))
        if p95 is None or baseline is None or baseline <= 0:
            return None
        return p95 / baseline
    key = _METRIC.get(kind)
    return None if key is None else _decimal(metrics.get(key))


# The largest value `insights.peak_strength numeric(20, 4)` holds; a larger reading would fail
# the upsert and drop the detector's findings. Every gate is far below it, so the cap hides nothing.
PEAK_STRENGTH_MAX = Decimal("9999999999999999")


def stored_strength(kind: str, metrics: Mapping[str, Any]) -> Decimal | None:
    """`strength` capped to what `insights.peak_strength` can store."""
    reading = strength(kind, metrics)
    return None if reading is None else min(reading, PEAK_STRENGTH_MAX)


def gate_for(kind: str, level: Level) -> Decimal | None:
    """The kind's gate at `level`; None at High recall and for a fixed or unknown check."""
    gate = GATES.get(kind)
    if gate is None or level == "high_recall":
        return None
    return gate.balanced if level == "balanced" else gate.high_precision


def passes(kind: str, level: Level, metrics: Mapping[str, Any]) -> bool:
    """Whether a finding with these metrics is kept at `level`."""
    gate = gate_for(kind, level)
    if gate is None:
        return True
    reading = strength(kind, metrics)
    return reading is None or reading >= gate
