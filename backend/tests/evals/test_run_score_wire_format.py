"""Score values are JSON numbers on every score shape (api-deviations, Scores).

Failure mode: an evaluator run or a test result serialises `value` as a decimal string while
trace scores send a number, so one client parser cannot read both.
"""

from decimal import Decimal

from app.api.schemas.evaluators import EvalScoreOut, RunScoreOut
from app.api.schemas.scores import ScoreOut
from app.db.models import Evaluator, Project
from app.db.rls import bind_project
from app.evals import runner
from tests.evals.judges import EVAL_INPUT, JudgeWorld, answer, create_judge, estimate_usage


def test_run_and_test_scores_are_json_numbers_like_trace_scores() -> None:
    run = RunScoreOut(name="x", value=Decimal("0.8"), string_value=None, comment=None)
    tested = EvalScoreOut(
        name="x", span_id=None, value=Decimal("0.8"), string_value=None, comment=None
    )

    for dumped in (run.model_dump(mode="json"), tested.model_dump(mode="json")):
        assert isinstance(dumped["value"], float)
        assert dumped["value"] == 0.8

    assert "value" in ScoreOut.model_fields
    assert ScoreOut.model_fields["value"].annotation == RunScoreOut.model_fields["value"].annotation


def test_a_categorical_score_keeps_a_null_value() -> None:
    run = RunScoreOut(name="tone", value=None, string_value="polite", comment=None)

    assert run.model_dump(mode="json")["value"] is None


async def test_run_scores_include_judge_comment(judge_world: JudgeWorld) -> None:
    """Failure mode: the judge's reasoning is stored on the score but the runs list drops it."""
    world = judge_world
    judge = await create_judge(world, budget="1")
    answer(world.fake, estimate_usage(judge))
    async with world.sessions() as db, db.begin():
        await bind_project(db, world.workspace.project_id)
        project = await db.get(Project, world.workspace.project_id)
        evaluator = await db.get(Evaluator, judge.evaluator_id)
    assert project is not None and evaluator is not None
    outcome = await runner.evaluate_item(
        world.deps,
        project,
        evaluator,
        trace_id=EVAL_INPUT.trace_id,
        span_id=EVAL_INPUT.span_id,
        input=EVAL_INPUT.input,
        output=EVAL_INPUT.output,
        expected_output=None,
    )
    assert outcome.status == "done", outcome

    url = f"/api/v1/projects/{world.workspace.project_id}/evaluators/{judge.evaluator_id}/runs"
    listed = await world.workspace.owner.get(url)
    assert listed.status_code == 200, listed.text
    [run] = listed.json()["items"]
    assert [score["comment"] for score in run["scores"]] == ["On topic."]
