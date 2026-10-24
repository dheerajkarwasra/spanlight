"""The catalogue's copy says what the detectors read."""

import pytest

from app.insights.catalogue import KIND_INFO


@pytest.mark.parametrize("kind", ["error_spike", "latency_regression"])
def test_baseline_summary_names_the_baseline_period(kind: str) -> None:
    # `context_queries`: the baseline is the 7 days that end where the 24-hour window starts.
    assert KIND_INFO[kind].summary.endswith(" over the 7 days before the last 24 hours.")
