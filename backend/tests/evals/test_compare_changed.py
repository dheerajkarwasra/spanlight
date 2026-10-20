"""Invariant: a pair whose frozen copies differ is marked and left out of the paired statistics.

Pure: builds `ItemRow`s directly, no database.
"""

import uuid
from decimal import Decimal
from typing import Any

from app.evals.compare import ItemRow, compare_runs

X = uuid.UUID("00000000-0000-0000-0000-000000000001")
Y = uuid.UUID("00000000-0000-0000-0000-000000000002")


def _row(
    item_id: uuid.UUID,
    score: float,
    *,
    expected: Any = "Paris",
    has_copy: bool = True,
) -> ItemRow:
    return ItemRow(
        dataset_item_id=item_id,
        trace_id=None,
        output={"text": "Paris", "finish_reason": "stop"},
        scores={"quality": score},
        latency_ms=100.0,
        cost_usd=Decimal("0.001"),
        error=None,
        input={"question": "Capital of France?"} if has_copy else None,
        expected_output=expected if has_copy else None,
        has_copy=has_copy,
    )


def test_changed_pair_is_marked_and_left_out() -> None:
    a = [_row(X, 0.2, expected="Paris"), _row(Y, 0.4)]
    b = [_row(X, 0.9, expected="Lyon"), _row(Y, 0.8)]

    comparison = compare_runs(a, b)

    by_id = {item.dataset_item_id: item for item in comparison.items}
    assert by_id[X].changed is True
    assert all(delta is None for delta in by_id[X].deltas.values())
    assert by_id[Y].changed is False
    assert by_id[Y].deltas == {"quality": Decimal("0.4000")}
    score = comparison.aggregate["quality"]
    assert (score.improved, score.regressed, score.unchanged) == (1, 0, 0)
    assert score.test is None  # one fair pair is fewer than two
    assert comparison.changed_items == 1
    assert score.mean_a == Decimal("0.3000")
    assert score.mean_b == Decimal("0.8500")


def test_metadata_only_difference_is_not_a_change() -> None:
    comparison = compare_runs([_row(X, 0.2)], [_row(X, 0.6)])

    item = comparison.items[0]
    assert item.changed is False
    assert item.deltas == {"quality": Decimal("0.4000")}
    assert comparison.changed_items == 0


def test_legacy_side_is_unknown_and_kept() -> None:
    a = [_row(X, 0.2, has_copy=False), _row(Y, 0.4, has_copy=False)]
    b = [_row(X, 0.9, expected="Lyon"), _row(Y, 0.8)]

    comparison = compare_runs(a, b)

    assert all(item.changed is None for item in comparison.items)
    assert comparison.items[0].deltas == {"quality": Decimal("0.7000")}
    score = comparison.aggregate["quality"]
    assert (score.improved, score.regressed, score.unchanged) == (2, 0, 0)
    assert score.test is not None and score.test.n == 2
    assert comparison.changed_items == 0
