"""Classify a span's usage so the UI can explain a missing cost.

Pure: no database, no settings. The state is derived from stored span columns when a span is
read, never persisted. Spec: docs/specs/2026-10-15-bet-4-otlp-on-ramps-design.md section 6.
"""

import enum
from decimal import Decimal

from app.db.models.telemetry import SpanKind


class UsageState(enum.StrEnum):
    PRICED = "priced"
    ROLLUP = "rollup"
    USAGE_NOT_REPORTED = "usage_not_reported"
    PARTIAL_USAGE = "partial_usage"
    MODEL_UNKNOWN = "model_unknown"
    NO_PRICE = "no_price"
    NOT_APPLICABLE = "not_applicable"


def usage_state(
    *,
    kind: SpanKind,
    model: str | None,
    input_tokens: int | None,
    output_tokens: int | None,
    cost_usd: Decimal | None,
    usage_rollup: bool,
) -> UsageState:
    """Return the usage state of one span.

    A rollup wins over a price: its usage is counted on its children even when the exporter
    priced it too (see docs/api-deviations.md). A price wins over every remaining state. The
    `llm` states follow the order of the spec table; any other kind is `not_applicable`.
    """
    if usage_rollup:
        return UsageState.ROLLUP
    if cost_usd is not None:
        return UsageState.PRICED
    if kind != SpanKind.LLM:
        return UsageState.NOT_APPLICABLE
    if model is None:
        return UsageState.MODEL_UNKNOWN
    if input_tokens is None and output_tokens is None:
        return UsageState.USAGE_NOT_REPORTED
    if input_tokens is None or output_tokens is None:
        return UsageState.PARTIAL_USAGE
    return UsageState.NO_PRICE
