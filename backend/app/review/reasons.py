"""The `reason_detail` stored with a selected queue item, one builder per reason. Pure.

A detail holds numbers, ids and the trace name only, never prompt or completion text: it is shown
on the annotate screen and listed by the queue API, and the trace itself is one click away. Money
and rates are 4-place strings. `DETAIL_KEYS` is the contract the queue output model and the tests
check against.
"""

import uuid
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

from app.db.models.annotations import ReviewReason

PLACES = Decimal("0.0001")

DETAIL_KEYS: dict[ReviewReason, frozenset[str]] = {
    ReviewReason.MANUAL: frozenset(),
    ReviewReason.DOCTOR_FINDING: frozenset({"insight_id", "kind"}),
    ReviewReason.ERROR: frozenset({"error_count", "first_error_span_id", "error_class"}),
    ReviewReason.COST_OUTLIER: frozenset({"cost_usd", "p99_usd", "trace_name", "sample"}),
    ReviewReason.RANDOM: frozenset({"rate"}),
}


def doctor_finding_detail(insight_id: uuid.UUID, kind: str) -> dict[str, Any]:
    return {"insight_id": str(insight_id), "kind": kind, "also": []}


def error_detail(
    error_count: int, first_error_span_id: str | None, error_class: str | None
) -> dict[str, Any]:
    return {
        "error_count": error_count,
        "first_error_span_id": first_error_span_id,
        "error_class": error_class,
        "also": [],
    }


def cost_outlier_detail(
    cost_usd: Decimal, p99_usd: Decimal, trace_name: str, sample: int
) -> dict[str, Any]:
    return {
        "cost_usd": _places(cost_usd),
        "p99_usd": _places(p99_usd),
        "trace_name": trace_name,
        "sample": sample,
        "also": [],
    }


def random_detail(rate: Decimal) -> dict[str, Any]:
    return {"rate": _places(rate), "also": []}


def _places(value: Decimal) -> str:
    return str(value.quantize(PLACES, rounding=ROUND_HALF_EVEN))
