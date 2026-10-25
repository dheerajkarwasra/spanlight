# ruff: noqa: RUF001, RUF002
"""Human–judge agreement statistics (`app/evals/agreement.py`): binning, confusion matrix, Cohen's
and quadratic-weighted kappa, F1 and the status rules. Every expected value is computed by hand."""

from decimal import Decimal

from app.evals.agreement import KAPPA_FLAG, MIN_PAIRS, agreement
from app.scores.types import ScoreDataType

D = Decimal
YES, NO = D(1), D(0)


def _boolean(yy: int, yn: int, ny: int, nn: int) -> list[tuple[Decimal, Decimal]]:
    """A 2x2 table as (human, judge) pairs: `yn` = human Yes, judge No."""
    return [(YES, YES)] * yy + [(YES, NO)] * yn + [(NO, YES)] * ny + [(NO, NO)] * nn


def test_perfect_numeric_agreement_is_kappa_1() -> None:
    values = [D("0.1"), D("0.3"), D("0.5"), D("0.7"), D("0.9")]
    pairs = [(v, v) for v in values for _ in range(6)]

    result = agreement(pairs, data_type=ScoreDataType.NUMERIC, min_value=D(0), max_value=D(1))

    assert result.status == "ok"
    assert result.pairs == 30
    assert result.kappa == D("1.0000")
    assert result.kappa_kind == "quadratic_weighted"
    assert result.matrix == tuple(tuple(6 if i == j else 0 for j in range(5)) for i in range(5))
    assert result.flagged is False


def test_29_pairs_is_not_enough() -> None:
    pairs = [(D("0.9"), D("0.9"))] * 29

    result = agreement(pairs, data_type=ScoreDataType.NUMERIC, min_value=None, max_value=None)

    assert MIN_PAIRS == 30
    assert result.status == "not_enough_pairs"
    assert result.pairs == 29
    assert result.kappa is None
    assert result.f1 is None
    assert result.flagged is False
    assert result.matrix[4][4] == 29
    assert sum(map(sum, result.matrix)) == 29


def test_one_class_is_undefined() -> None:
    result = agreement(
        [(YES, YES)] * 30, data_type=ScoreDataType.BOOLEAN, min_value=None, max_value=None
    )

    assert result.status == "undefined"
    assert result.kappa is None
    assert result.flagged is False
    assert result.matrix == ((0, 0), (0, 30))


def test_boolean_uses_cohen_and_f1_yes_positive() -> None:
    """po = 30/40 = 0.75; pe = (25*25 + 15*15)/1600 = 0.53125;
    kappa = (0.75 - 0.53125)/(1 - 0.53125) = 0.21875/0.46875 = 0.4667;
    F1 = 2*20/(2*20 + 5 + 5) = 40/50 = 0.8."""
    result = agreement(
        _boolean(yy=20, yn=5, ny=5, nn=10),
        data_type=ScoreDataType.BOOLEAN,
        min_value=None,
        max_value=None,
    )

    assert result.status == "ok"
    assert result.kappa_kind == "cohen"
    assert result.kappa == D("0.4667")
    assert result.f1 == D("0.8000")
    assert result.labels == ("No", "Yes")
    assert result.matrix == ((10, 5), (5, 20))
    assert result.pass_threshold is None
    assert result.flagged is True


def test_independent_ratings_give_kappa_0() -> None:
    """Every cell 10 of 40: po = 0.5 = pe, so kappa = 0."""
    result = agreement(
        _boolean(yy=10, yn=10, ny=10, nn=10),
        data_type=ScoreDataType.BOOLEAN,
        min_value=None,
        max_value=None,
    )

    assert result.kappa == D("0.0000")


def test_complete_disagreement_gives_kappa_minus_1() -> None:
    """0/20/20/0: po = 0, pe = 0.5, so kappa = -0.5/0.5 = -1."""
    result = agreement(
        _boolean(yy=0, yn=20, ny=20, nn=0),
        data_type=ScoreDataType.BOOLEAN,
        min_value=None,
        max_value=None,
    )

    assert result.status == "ok"
    assert result.kappa == D("-1.0000")
    assert result.flagged is True


def test_weighted_kappa_known_value() -> None:
    """30 pairs over [0, 1], 5 bins, w_ij = (i-j)^2/16:
    10 x (0.1, 0.1) -> cell (0,0); 10 x (0.5, 0.5) -> (2,2); 10 x (0.9, 0.5) -> (4,2).
    Rows (human) 10/10/10 at bins 0/2/4; columns (judge) 10 at bin 0, 20 at bin 2.
    sum w*O = 10 * 4/16 = 2.5.
    E = row*col/30: 10/3 in column 0, 20/3 in column 2.
    sum w*E = (4/16)(20/3) + (4/16)(10/3) + (16/16)(10/3) + (4/16)(20/3) = 45/6 = 7.5.
    kappa = 1 - 2.5/7.5 = 0.6667.
    A value at the 0.5 threshold passes: tp = 20 and nothing else, so F1 = 1."""
    pairs = [(D("0.1"), D("0.1"))] * 10 + [(D("0.5"), D("0.5"))] * 10 + [(D("0.9"), D("0.5"))] * 10

    result = agreement(pairs, data_type=ScoreDataType.NUMERIC, min_value=D(0), max_value=D(1))

    assert result.status == "ok"
    assert result.kappa == D("0.6667")
    assert result.f1 == D("1.0000")
    assert result.matrix[4][2] == 10
    assert result.flagged is False


def test_low_kappa_is_flagged() -> None:
    """16/4/4/16: po = 0.8, pe = 0.5, kappa = 0.6 (not flagged).
    15/5/5/15: po = 0.75, pe = 0.5, kappa = 0.5 (flagged)."""
    at_flag = agreement(
        _boolean(16, 4, 4, 16), data_type=ScoreDataType.BOOLEAN, min_value=None, max_value=None
    )
    below = agreement(
        _boolean(15, 5, 5, 15), data_type=ScoreDataType.BOOLEAN, min_value=None, max_value=None
    )

    assert at_flag.kappa == KAPPA_FLAG
    assert at_flag.flagged is False
    assert below.kappa == D("0.5000")
    assert below.flagged is True


def test_values_clamp_into_scale_and_default_scale_is_0_1() -> None:
    pairs = [(D("1.2"), D("1.0"))] * 15 + [(D("-0.3"), D(0))] * 15

    result = agreement(pairs, data_type=ScoreDataType.NUMERIC, min_value=None, max_value=None)

    assert result.pass_threshold == D("0.5")
    assert result.matrix[4][4] == 15
    assert result.matrix[0][0] == 15
    assert result.kappa == D("1.0000")


def test_labels_are_bin_edges() -> None:
    unit = agreement([], data_type=ScoreDataType.NUMERIC, min_value=None, max_value=None)
    tens = agreement([], data_type=ScoreDataType.NUMERIC, min_value=D("0.0"), max_value=D("10.0"))

    assert unit.labels == ("0–0.2", "0.2–0.4", "0.4–0.6", "0.6–0.8", "0.8–1")
    assert tens.labels == ("0–2", "2–4", "4–6", "6–8", "8–10")
    assert unit.matrix == ()


def test_categorical_is_unsupported() -> None:
    result = agreement(
        [(D(1), D(1))] * 30, data_type=ScoreDataType.CATEGORICAL, min_value=None, max_value=None
    )

    assert result.status == "unsupported"
    assert result.pairs == 30
    assert result.kappa is None
    assert result.kappa_kind is None
    assert result.matrix == ()


def test_zero_width_scale_is_unsupported() -> None:
    result = agreement(
        [(D(1), D(1))] * 30, data_type=ScoreDataType.NUMERIC, min_value=D(1), max_value=D(1)
    )

    assert result.status == "unsupported"
    assert result.kappa is None
    assert result.f1 is None


def test_f1_none_without_positives() -> None:
    pairs = [(D("0.1"), D("0.3"))] * 15 + [(D("0.3"), D("0.1"))] * 15

    result = agreement(pairs, data_type=ScoreDataType.NUMERIC, min_value=D(0), max_value=D(1))

    assert result.status == "ok"
    assert result.f1 is None
    assert result.kappa is not None
