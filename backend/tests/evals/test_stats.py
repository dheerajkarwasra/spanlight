"""Invariant: the clustered standard error counts a cluster of perfectly correlated items as one
observation, and reduces to the naive CLT standard error when every item is its own cluster.

Pure: no database.
"""

import math
from decimal import Decimal

from app.evals.stats import Interval, clustered_mean_interval, clustered_paired_test


def test_clustered_se_is_the_two_point_se_for_correlated_clusters_and_naive_for_singletons() -> (
    None
):
    before = [0] * 6
    after = [1, 1, 1, 0, 0, 0]
    two_point = math.sqrt(0.5**2 + 0.5**2) / 2  # the SE of the two points 1 and 0
    naive = math.sqrt(0.5 * 0.5 / 6)

    correlated = clustered_paired_test(before, after, ["x", "x", "x", "y", "y", "y"])
    assert correlated is not None and correlated.clusters == 2
    assert math.isclose(correlated.standard_error, two_point)
    assert correlated.standard_error > naive

    singletons = clustered_paired_test(before, after, list(range(6)))
    assert singletons is not None and singletons.clusters == 6
    assert math.isclose(singletons.standard_error, naive)
    assert singletons.z is not None and math.isclose(singletons.z, 0.5 / naive)
    assert singletons.p_value is not None
    assert math.isclose(singletons.p_value, math.erfc(0.5 / naive / math.sqrt(2)))

    # Every difference equal: the SE is 0 and the p-value is undefined, never 0.
    constant = clustered_paired_test([0, 0, 0], [1, 1, 1], ["a", "b", "c"])
    assert constant is not None and constant.p_value is None and not constant.significant

    # Pass rate 0.3 of ten singletons: mean ± 1.959964 · sqrt(0.21 / 10).
    interval, clusters = clustered_mean_interval([1] * 3 + [0] * 7, list(range(10)))
    assert clusters == 10
    assert interval == Interval(low=Decimal("0.0160"), high=Decimal("0.5840"))
