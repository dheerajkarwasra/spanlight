"""Exact paired-test results and the CPU budget for a full 10,000-item dataset."""

from decimal import Decimal
from time import perf_counter

import pytest

from app.evals.stats import mcnemar_exact, sign_test


@pytest.mark.parametrize("test", [mcnemar_exact, sign_test])
@pytest.mark.parametrize(
    ("a", "b", "p_value", "significant"),
    [
        (0, 0, "1.0000", False),
        (1, 0, "1.0000", False),
        (5, 0, "0.0625", False),
        (6, 0, "0.0312", True),
        (0, 6, "0.0312", True),
        (3, 3, "1.0000", False),
        (4, 3, "1.0000", False),
        (2, 8, "0.1094", False),
    ],
)
def test_paired_tests_preserve_exact_significance_and_half_even_rounding(
    test, a: int, b: int, p_value: str, significant: bool
) -> None:
    result = test(a, b, unchanged=17)
    assert result.n == a + b
    assert result.unchanged == 17
    assert result.p_value == Decimal(p_value)
    assert result.significant is significant


@pytest.mark.serial
def test_full_dataset_comparison_does_not_block_for_seconds() -> None:
    start = perf_counter()
    result = sign_test(5500, 4500)
    elapsed = perf_counter() - start

    assert result.n == 10_000
    assert result.p_value == Decimal("0.0000")
    assert result.significant is True
    # The normal path takes milliseconds; 500 ms leaves room for shared CI runners.
    assert elapsed < 0.5
