"""Histogram estimates must track continuous percentiles without inventing precision."""

from statistics import quantiles

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from app.rollups.buckets import BOUNDS_MS, BUCKET_COUNT, approx_percentile, bucket_index, merge


def histogram(values: list[float]) -> list[int]:
    counts = [0] * BUCKET_COUNT
    for value in values:
        counts[bucket_index(value)] += 1
    return counts


@pytest.mark.parametrize(
    ("values", "q", "exact"),
    [
        ([2.0, 2000.0], 0.5, 1001.0),
        ([2.0] * 19 + [2000.0], 0.95, 101.9),
        ([100.0] * 100, 0.95, 100.0),
    ],
)
def test_sparse_and_concentrated_percentiles_stay_within_interior_error(
    values: list[float], q: float, exact: float
) -> None:
    estimate = approx_percentile(histogram(values), q)
    assert estimate == pytest.approx(exact, rel=0.201)


@settings(max_examples=200)
@given(
    values=st.lists(
        st.floats(min_value=1.000001, max_value=BOUNDS_MS[-2]), min_size=2, max_size=100
    ),
    index=st.sampled_from([0, 9, 18]),
)
def test_interior_estimates_match_independent_inclusive_quantiles(
    values: list[float], index: int
) -> None:
    exact = quantiles(values, n=20, method="inclusive")[index]
    estimate = approx_percentile(histogram(values), (index + 1) / 20)
    assert estimate == pytest.approx(exact, rel=0.201)


def test_large_count_median_preserves_the_rank_between_two_populations() -> None:
    counts = [0] * BUCKET_COUNT
    counts[bucket_index(2.0)] = 2**53
    counts[bucket_index(2000.0)] = 2**53
    assert approx_percentile(counts, 0.5) == pytest.approx(1001.0, rel=0.201)


@given(
    left=st.lists(st.floats(min_value=0, max_value=1_000_000), max_size=60),
    right=st.lists(st.floats(min_value=0, max_value=1_000_000), max_size=60),
)
def test_merging_histograms_preserves_the_estimate(left: list[float], right: list[float]) -> None:
    assert approx_percentile(merge(histogram(left), histogram(right)), 0.95) == approx_percentile(
        histogram(left + right), 0.95
    )


@given(values=st.lists(st.floats(min_value=0, max_value=1_000_000), min_size=1, max_size=100))
@example(values=[0.0, 2.0])
def test_estimates_are_monotone_and_stay_in_the_stored_range(values: list[float]) -> None:
    counts = histogram(values)
    estimates = [approx_percentile(counts, q) for q in (0.0, 0.05, 0.5, 0.95, 1.0)]
    assert all(value is not None for value in estimates)
    assert estimates == sorted(estimates)
    assert 0 <= estimates[0] <= estimates[-1] <= 300_000


def test_empty_histogram_remains_unknown() -> None:
    assert approx_percentile([0] * BUCKET_COUNT, 0.95) is None


@pytest.mark.parametrize("value", [0.0, 1.0])
def test_submillisecond_estimate_limits_absolute_error_to_half_a_millisecond(value: float) -> None:
    estimate = approx_percentile(histogram([value] * 20), 0.95)
    assert estimate is not None
    assert abs(estimate - value) <= 0.5


@pytest.mark.parametrize("bad", [-1, 0.5, float("nan"), True])
def test_invalid_histogram_counts_are_rejected(bad: object) -> None:
    counts = [0] * BUCKET_COUNT
    counts[3] = bad
    with pytest.raises(ValueError, match="nonnegative integers"):
        approx_percentile(counts, 0.95)


@pytest.mark.parametrize("q", [-0.01, 1.01, float("nan")])
def test_invalid_quantiles_are_rejected(q: float) -> None:
    with pytest.raises(ValueError, match="q must be between"):
        approx_percentile([0] * BUCKET_COUNT, q)
