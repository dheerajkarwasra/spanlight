"""Shared alert vocabulary. Pure: no database or framework imports."""

import uuid
from dataclasses import dataclass
from enum import StrEnum


class Metric(StrEnum):
    ERROR_RATE = "error_rate"
    P95_MS = "p95_ms"
    TTFT_P95_MS = "ttft_p95_ms"
    COST_USD = "cost_usd"
    LLM_CALLS = "llm_calls"
    TOKENS = "tokens"
    SPEND = "spend"
    ERROR_CALLS = "error_calls"


class Comparator(StrEnum):
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"


class AlertRuleKind(StrEnum):
    THRESHOLD = "threshold"
    ANOMALY = "anomaly"
    BUDGET = "budget"


class RuleStateName(StrEnum):
    OK = "ok"
    FIRING = "firing"


@dataclass(frozen=True, slots=True)
class MetricFilters:
    """Which spans a metric reads. Every field is an exact match; ``None`` means any value.

    ``environment`` is the trace's environment. ``source_key_id`` (the gateway key a call came
    through), ``external_user_id`` (the trace's user), ``metadata`` (a key and value the trace's
    metadata contains) and ``trace_name`` (the trace's name) exist only on the raw rows, so any
    of them makes the whole window read raw spans. ``error_class`` and ``rate_limit_kind`` narrow
    only the failed calls that ``error_calls`` and ``error_rate`` count, never the calls a rate
    is divided by. Frozen and hashable: it is part of a cache key.
    """

    environment: str | None = None
    provider: str | None = None
    model: str | None = None
    source_key_id: uuid.UUID | None = None
    external_user_id: str | None = None
    metadata: tuple[str, str] | None = None
    trace_name: str | None = None
    error_class: str | None = None
    rate_limit_kind: str | None = None

    @property
    def narrows_errors(self) -> bool:
        """Whether only the failed calls of one error class or rate-limit kind count."""
        return self.error_class is not None or self.rate_limit_kind is not None

    @property
    def forces_raw(self) -> bool:
        """Whether a filter lives only on the raw rows, so no rollup can serve the window."""
        return (
            self.source_key_id is not None
            or self.external_user_id is not None
            or self.metadata is not None
            or self.trace_name is not None
        )
