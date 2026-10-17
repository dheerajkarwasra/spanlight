"""Helpers shared by the baseline-spike detectors (`error_spike`, `latency_regression`,
`cost_spike`) and `rate_limit_pressure`: the spike rule, an exact percentile, fault and
gateway-refusal exclusion and grouping.

All of it is pure; nothing here reads a clock or a database.
"""

import math
from collections.abc import Callable, Iterable, Sequence
from decimal import Decimal

from app.ingest.rate_limit_kind import RateLimitKind
from app.insights.context import LlmSpanRow

# The kind of a refusal by Spanlight's own key limiter (`app.gateway.refusal_span`).
GATEWAY_REFUSAL_KIND = RateLimitKind.GATEWAY.value


def exceeds(current: Decimal, baseline: Decimal, ratio: Decimal, floor: Decimal) -> bool:
    """True when `current >= max(ratio * baseline, baseline + floor)`.

    The multiple alone fires on noise when the baseline is tiny (3 x 0.2 % is 0.6 %); the absolute
    floor alone misses a large baseline doubling. Needing both is the same as exceeding the larger.
    """
    return current >= max(ratio * baseline, baseline + floor)


def percentile_nearest_rank(values: Sequence[int], percentile: Decimal) -> Decimal:
    """Exact nearest-rank percentile: the value at rank `ceil(p/100 * n)` of the sorted values.

    Always one of the observed values (no interpolation), so the result is reproducible and never
    invents a latency nobody measured. `values` must not be empty.
    """
    ordered = sorted(values)
    rank = math.ceil(percentile / Decimal(100) * len(ordered))
    return Decimal(ordered[max(rank, 1) - 1])


def without_faults(spans: Iterable[LlmSpanRow]) -> list[LlmSpanRow]:
    """Drop fault-injected spans: these detectors judge real traffic, not lab scenarios."""
    return [span for span in spans if span.fault_scenario is None]


def provider_traffic(spans: Iterable[LlmSpanRow]) -> list[LlmSpanRow]:
    """Drop fault-injected spans and the gateway key limiter's own refusals.

    A refusal (`rate_limit_kind == "gateway"`) never reached a provider and has no model, so it
    says nothing about a model's error rate or a provider's limits.
    """
    return [span for span in without_faults(spans) if span.rate_limit_kind != GATEWAY_REFUSAL_KIND]


def group_spans[K](
    spans: Iterable[LlmSpanRow], key: Callable[[LlmSpanRow], K]
) -> dict[K, list[LlmSpanRow]]:
    """Spans bucketed by `key`, buckets in first-seen order."""
    groups: dict[K, list[LlmSpanRow]] = {}
    for span in spans:
        groups.setdefault(key(span), []).append(span)
    return groups
