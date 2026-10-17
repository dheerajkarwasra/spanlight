"""The completeness ratio: stored / (stored + every loss), a lower bound on overlap."""

from decimal import Decimal

from app.telemetry_losses.completeness import CompletenessResult, ratio


def test_ratio_rules() -> None:
    # Nothing stored, nothing lost: unknown, not 100 % and not 0 %.
    assert ratio(0, {}) == CompletenessResult(completeness=None, lower_bound=False)
    assert ratio(0, {("gateway", "overflow"): 0}).completeness is None

    # Nothing stored but something lost: exactly zero.
    zero = ratio(0, {("gateway", "overflow"): 3})
    assert zero == CompletenessResult(completeness=Decimal("0"), lower_bound=False)
    assert str(zero.completeness) == "0"

    # stored / (stored + Σ losses), four places, rounded down.
    two_thirds = ratio(2, {("gateway", "error"): 1})
    assert str(two_thirds.completeness) == "0.6666"
    assert str(ratio(7, {}).completeness) == "1.0000"
    assert str(ratio(9_999, {("ingest", "validation"): 1}).completeness) == "0.9999"
    # 19,999 / 20,000 = 0.99995 and 1 / 32 = 0.03125: both round down.
    assert str(ratio(19_999, {("ingest", "validation"): 1}).completeness) == "0.9999"
    assert str(ratio(1, {("sdk", "dropped"): 31}).completeness) == "0.0312"

    # lower_bound only when sdk failed and an ingest rejection are both non-zero.
    both = ratio(90, {("sdk", "failed"): 5, ("ingest", "usage"): 5})
    assert both == CompletenessResult(completeness=Decimal("0.9000"), lower_bound=True)
    assert ratio(90, {("sdk", "failed"): 5}).lower_bound is False
    assert ratio(90, {("ingest", "usage"): 5}).lower_bound is False
    assert ratio(90, {("sdk", "failed"): 5, ("ingest", "usage"): 0}).lower_bound is False
    assert ratio(90, {("sdk", "dropped"): 5, ("ingest", "usage"): 5}).lower_bound is False


def test_every_source_counts_toward_the_denominator() -> None:
    losses = {
        ("gateway", "overflow"): 1,
        ("gateway", "error"): 1,
        ("ingest", "too_old"): 1,
        ("sdk", "dropped"): 1,
    }
    assert str(ratio(6, losses).completeness) == "0.6000"


def test_any_loss_keeps_the_ratio_below_one() -> None:
    for stored in (1, 9_999, 19_999, 10**9, 10**15):
        result = ratio(stored, {("gateway", "overflow"): 1})
        assert result.completeness is not None
        assert result.completeness < Decimal("1.0000"), stored
