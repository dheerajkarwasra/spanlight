"""Doctor precision arithmetic: the Wilson interval and the target, on hand-computed values."""

from decimal import Decimal

import pytest

from app.insights.precision import (
    _Z95,
    MIN_FEEDBACK,
    PRECISION_TARGET,
    PROVIDER_PATH_KINDS,
    WILSON_LOW_TARGET,
    PrecisionRow,
    wilson,
)


def test_wilson_matches_hand_computed_values() -> None:
    assert wilson(18, 20) == (Decimal("0.6990"), Decimal("0.9721"))
    assert wilson(0, 5) == (Decimal("0.0000"), Decimal("0.4345"))
    assert wilson(5, 5) == (Decimal("0.5655"), Decimal("1.0000"))
    assert wilson(17, 20) == (Decimal("0.6396"), Decimal("0.9476"))
    assert wilson(16, 20) == (Decimal("0.5840"), Decimal("0.9193"))


def test_wilson_bounds_have_four_places() -> None:
    bounds = wilson(18, 20)
    assert bounds is not None
    assert all(bound.as_tuple().exponent == -4 for bound in bounds)


def test_wilson_is_none_without_sample() -> None:
    assert wilson(0, 0) is None


def test_targets_are_the_spec_values() -> None:
    assert Decimal("0.8") == PRECISION_TARGET
    assert Decimal("0.6") == WILSON_LOW_TARGET
    assert MIN_FEEDBACK == 20


def test_18_of_20_meets_target() -> None:
    row = PrecisionRow(kind="error_spike", right=18, wrong=2)
    assert row.n == 20
    assert row.precision == Decimal("0.9000")
    assert (row.wilson_low, row.wilson_high) == (Decimal("0.6990"), Decimal("0.9721"))
    assert row.meets_target is True


def test_17_of_20_meets_target() -> None:
    row = PrecisionRow(kind="retry_storm", right=17, wrong=3)
    assert row.precision == Decimal("0.8500")
    assert row.wilson_low == Decimal("0.6396")
    assert row.meets_target is True


def test_16_of_20_misses_target_on_the_lower_bound() -> None:
    row = PrecisionRow(kind="retry_storm", right=16, wrong=4)
    assert row.precision == Decimal("0.8000")
    assert row.wilson_low == Decimal("0.5840")
    assert row.meets_target is False


def test_17_of_21_misses_target_though_its_low_bound_shows_0_6000() -> None:
    # The exact low bound is 0.59999...: it rounds to the target for display, but the target is
    # compared unrounded, so nobody rounds across the line.
    row = PrecisionRow(kind="retry_storm", right=17, wrong=4)
    assert row.n == 21
    assert row.precision == Decimal("0.8095")
    assert row.wilson_low == Decimal("0.6000")
    assert row.meets_target is False


def test_19_of_19_misses_target_below_min_feedback() -> None:
    row = PrecisionRow(kind="provider_incident", right=19, wrong=0)
    assert row.precision == Decimal("1.0000")
    assert row.meets_target is False


def test_precision_is_none_at_zero_n() -> None:
    row = PrecisionRow(kind="error_spike", right=0, wrong=0)
    assert row.n == 0
    assert row.precision is None
    assert row.wilson_low is None
    assert row.wilson_high is None
    assert row.meets_target is False


def test_precision_rounds_half_up_to_four_places() -> None:
    assert PrecisionRow(kind="error_spike", right=2, wrong=1).precision == Decimal("0.6667")


def test_provider_path_kinds_are_the_eight_spec_kinds() -> None:
    assert (
        frozenset(
            {
                "error_spike",
                "retry_storm",
                "retry_after_ignored",
                "rate_limit_pressure",
                "provider_incident",
                "client_timeout_misconfigured",
                "truncated_stream_accepted",
                "unsupported_parameter_retried",
            }
        )
        == PROVIDER_PATH_KINDS
    )


@pytest.mark.parametrize(("successes", "total"), [(-1, 5), (6, 5), (1, -1)])
def test_wilson_rejects_counts_outside_the_sample(successes: int, total: int) -> None:
    with pytest.raises(ValueError, match="successes"):
        wilson(successes, total)


@pytest.mark.parametrize(("right", "wrong"), [(-1, 0), (0, -1)])
def test_a_precision_row_rejects_negative_counts(right: int, wrong: int) -> None:
    with pytest.raises(ValueError, match="negative"):
        PrecisionRow(kind="error_spike", right=right, wrong=wrong)


def test_the_default_z_is_the_95_percent_constant() -> None:
    assert _Z95 == 1.96
    assert wilson(18, 20) == wilson(18, 20, _Z95)
