"""Score values are JSON numbers on every score shape (api-deviations, Scores).

Failure mode: an evaluator run or a test result serialises `value` as a decimal string while
trace scores send a number, so one client parser cannot read both.
"""

from decimal import Decimal

from app.api.schemas.evaluators import EvalScoreOut, RunScoreOut
from app.api.schemas.scores import ScoreOut


def test_run_and_test_scores_are_json_numbers_like_trace_scores() -> None:
    run = RunScoreOut(name="x", value=Decimal("0.8"), string_value=None)
    tested = EvalScoreOut(
        name="x", span_id=None, value=Decimal("0.8"), string_value=None, comment=None
    )

    for dumped in (run.model_dump(mode="json"), tested.model_dump(mode="json")):
        assert isinstance(dumped["value"], float)
        assert dumped["value"] == 0.8

    assert "value" in ScoreOut.model_fields
    assert ScoreOut.model_fields["value"].annotation == RunScoreOut.model_fields["value"].annotation


def test_a_categorical_score_keeps_a_null_value() -> None:
    run = RunScoreOut(name="tone", value=None, string_value="polite")

    assert run.model_dump(mode="json")["value"] is None
