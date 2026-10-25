"""Human-judge agreement read from stored scores (UX Phase 6, rulings 1-4).

Failure modes: a stale score on either side pairs instead of the newest one; an API-key score or a
score under another name counts as a review or a verdict; a judge's test-run scores are left out;
the 2,000-pair cap drops the newest pairs or hides that it applied; a heuristic evaluator gets a
badge; another project's evaluator answers instead of 404; a judge's verdicts from before its
model changed still pair.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.rls import bypass_rls
from app.evals.agreement import MAX_PAIRS
from tests.conftest import BrowserFactory
from tests.evals.judges import JudgeWorld, create_judge
from tests.evals.runs import insert_run
from tests.helpers import Workspace, create_workspace, new_trace_id

Sessions = async_sessionmaker[AsyncSession]


async def _sql(sessions: Sessions, statement: str, params: dict[str, Any]) -> None:
    async with sessions() as db, db.begin():
        await bypass_rls(db)
        await db.execute(text(statement), params)


async def add_evaluator(
    sessions: Sessions, project_id: str, name: str, kind: str = "llm_judge"
) -> uuid.UUID:
    evaluator_id = uuid.uuid4()
    await _sql(
        sessions,
        "INSERT INTO evaluators (id, project_id, name, kind, config, monthly_budget_usd) "
        "VALUES (:id, :project_id, :name, :kind, "
        "jsonb_build_object('kind', CAST(:kind AS text)), 1)",
        {"id": evaluator_id, "project_id": project_id, "name": name, "kind": kind},
    )
    return evaluator_id


async def add_score(
    sessions: Sessions,
    project_id: str,
    trace_id: str,
    value: int | float,
    *,
    name: str = "judge",
    source: str = "human",
    run_id: uuid.UUID | None = None,
    minutes_ago: int = 0,
) -> None:
    await _sql(
        sessions,
        "INSERT INTO scores (id, project_id, trace_id, name, data_type, source, value, "
        "evaluator_run_id, created_at) VALUES (gen_random_uuid(), :project_id, :trace_id, :name, "
        "'numeric', :source, :value, CAST(:run_id AS uuid), "
        "now() - make_interval(mins => :minutes_ago))",
        {
            "project_id": project_id,
            "trace_id": trace_id,
            "name": name,
            "source": source,
            "value": value,
            "run_id": run_id,
            "minutes_ago": minutes_ago,
        },
    )


async def judge_world(
    browser_factory: BrowserFactory, sessions: Sessions
) -> tuple[Workspace, uuid.UUID, uuid.UUID]:
    """A workspace with one `judge` LLM-judge evaluator and one online run of it."""
    workspace = await create_workspace(await browser_factory("owner@example.com"))
    evaluator_id = await add_evaluator(sessions, workspace.project_id, "judge")
    run_id = await insert_run(sessions, evaluator_id, started_at=datetime.now(UTC), source="online")
    return workspace, evaluator_id, run_id


def _url(workspace: Workspace, evaluator_id: uuid.UUID) -> str:
    return f"/api/v1/projects/{workspace.project_id}/evaluators/{evaluator_id}/agreement"


async def test_agreement_pairs_newest_score_per_side(
    browser_factory: BrowserFactory, session_factory: Sessions
) -> None:
    workspace, evaluator_id, run_id = await judge_world(browser_factory, session_factory)
    project, trace = workspace.project_id, new_trace_id()
    await add_score(session_factory, project, trace, 0, minutes_ago=10)
    await add_score(session_factory, project, trace, 1, minutes_ago=1)
    await add_score(session_factory, project, trace, 0, source="eval", run_id=run_id, minutes_ago=9)
    await add_score(session_factory, project, trace, 1, source="eval", run_id=run_id, minutes_ago=2)
    # Reviewed but never judged, and judged but never reviewed: neither is a pair.
    await add_score(session_factory, project, new_trace_id(), 1)
    await add_score(session_factory, project, new_trace_id(), 1, source="eval", run_id=run_id)

    response = await workspace.owner.get(_url(workspace, evaluator_id))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body == {
        "evaluator_id": str(evaluator_id),
        "score_name": "judge",
        # No score config yet: read as numeric over [0, 1].
        "data_type": None,
        "status": "not_enough_pairs",
        "pairs": 1,
        "min_pairs": 30,
        "capped": False,
        "kappa": None,
        "kappa_kind": "quadratic_weighted",
        "f1": None,
        "pass_threshold": 0.5,
        "labels": ["0–0.2", "0.2–0.4", "0.4–0.6", "0.6–0.8", "0.8–1"],  # noqa: RUF001
        "matrix": [[0] * 5] * 4 + [[0, 0, 0, 0, 1]],
        "flagged": False,
        "since": None,
    }


async def test_agreement_reads_the_score_config(
    browser_factory: BrowserFactory, session_factory: Sessions
) -> None:
    workspace, evaluator_id, run_id = await judge_world(browser_factory, session_factory)
    await _sql(
        session_factory,
        "INSERT INTO score_configs (id, project_id, name, data_type) "
        "VALUES (gen_random_uuid(), :project_id, 'judge', 'boolean')",
        {"project_id": workspace.project_id},
    )
    trace = new_trace_id()
    await add_score(session_factory, workspace.project_id, trace, 1)
    await add_score(session_factory, workspace.project_id, trace, 0, source="eval", run_id=run_id)

    body = (await workspace.owner.get(_url(workspace, evaluator_id))).json()

    assert (body["data_type"], body["kappa_kind"], body["labels"]) == (
        "boolean",
        "cohen",
        ["No", "Yes"],
    )
    assert (body["pass_threshold"], body["matrix"]) == (None, [[0, 0], [1, 0]])


async def test_agreement_ignores_api_scores_and_other_names(
    browser_factory: BrowserFactory, session_factory: Sessions
) -> None:
    workspace, evaluator_id, run_id = await judge_world(browser_factory, session_factory)
    project = workspace.project_id
    api_trace, renamed_trace = new_trace_id(), new_trace_id()
    await add_score(session_factory, project, api_trace, 1, source="api")
    await add_score(session_factory, project, api_trace, 1, source="eval", run_id=run_id)
    await add_score(session_factory, project, renamed_trace, 1, name="other")
    await add_score(session_factory, project, renamed_trace, 1, source="eval", run_id=run_id)

    body = (await workspace.owner.get(_url(workspace, evaluator_id))).json()

    assert (body["pairs"], body["matrix"]) == (0, [])


async def test_agreement_uses_runs_of_any_source(
    browser_factory: BrowserFactory, session_factory: Sessions
) -> None:
    workspace, evaluator_id, online_run = await judge_world(browser_factory, session_factory)
    runs = [
        online_run,
        await insert_run(session_factory, evaluator_id, started_at=datetime.now(UTC)),
        await insert_run(
            session_factory, evaluator_id, started_at=datetime.now(UTC), source="experiment"
        ),
    ]
    for run_id in runs:
        trace = new_trace_id()
        await add_score(session_factory, workspace.project_id, trace, 1)
        await add_score(
            session_factory, workspace.project_id, trace, 1, source="eval", run_id=run_id
        )

    body = (await workspace.owner.get(_url(workspace, evaluator_id))).json()

    assert body["pairs"] == 3


async def test_agreement_caps_at_2000_pairs_and_says_so(
    browser_factory: BrowserFactory, session_factory: Sessions
) -> None:
    workspace, evaluator_id, run_id = await judge_world(browser_factory, session_factory)
    # 2,001 agreeing pairs; the oldest review (g = 2001) is the only one in bin 0, so it must be
    # the pair the cap drops.
    await _sql(
        session_factory,
        "INSERT INTO scores (id, project_id, trace_id, name, data_type, source, value, "
        "evaluator_run_id, created_at) "
        "SELECT gen_random_uuid(), :project_id, lpad(to_hex(g), 32, '0'), 'judge', 'numeric', "
        "side.source::score_source, CASE WHEN g = :total THEN 0 ELSE 1 END, "
        "CASE WHEN side.source = 'eval' THEN CAST(:run_id AS uuid) END, "
        "now() - make_interval(secs => g) "
        "FROM generate_series(1, :total) AS g, (VALUES ('human'), ('eval')) AS side(source)",
        {"project_id": workspace.project_id, "run_id": run_id, "total": MAX_PAIRS + 1},
    )

    body = (await workspace.owner.get(_url(workspace, evaluator_id))).json()

    assert (body["pairs"], body["capped"]) == (MAX_PAIRS, True)
    assert body["matrix"][0][0] == 0
    assert body["matrix"][4][4] == MAX_PAIRS


async def test_agreement_counts_only_verdicts_since_the_judge_changed(
    judge_world: JudgeWorld,
) -> None:
    """A model change resets the judge side; a sampling-rate change does not."""
    world, sessions = judge_world, judge_world.sessions
    judge = await create_judge(world, budget="5")
    project = world.workspace.project_id
    url = f"/api/v1/projects/{project}/evaluators/{judge.evaluator_id}"
    old_run = await insert_run(
        sessions, judge.evaluator_id, started_at=world.clock.wall() - timedelta(hours=1)
    )
    old_trace, new_trace = new_trace_id(), new_trace_id()
    await add_score(sessions, str(project), old_trace, 1, name="relevance")
    await add_score(
        sessions, str(project), old_trace, 1, name="relevance", source="eval", run_id=old_run
    )

    changed = await world.workspace.owner.patch(url, json={"config": {"model": "gpt-4.1"}})
    assert changed.status_code == 200, changed.text
    since = world.clock.wall()
    new_run = await insert_run(sessions, judge.evaluator_id, started_at=since)
    await add_score(sessions, str(project), new_trace, 0, name="relevance")
    await add_score(
        sessions, str(project), new_trace, 0, name="relevance", source="eval", run_id=new_run
    )
    world.clock.advance(60)
    resampled = await world.workspace.owner.patch(url, json={"sampling_rate": 0.5})
    assert resampled.status_code == 200, resampled.text

    body = (await world.workspace.owner.get(f"{url}/agreement")).json()

    assert body["pairs"] == 1
    assert body["matrix"][0][0] == 1
    assert datetime.fromisoformat(body["since"]) == since


async def test_summary_lists_only_judges(
    browser_factory: BrowserFactory, session_factory: Sessions
) -> None:
    workspace, judge_id, run_id = await judge_world(browser_factory, session_factory)
    earlier_id = await add_evaluator(session_factory, workspace.project_id, "accuracy")
    await add_evaluator(session_factory, workspace.project_id, "length", kind="heuristic")
    trace = new_trace_id()
    await add_score(session_factory, workspace.project_id, trace, 1)
    await add_score(session_factory, workspace.project_id, trace, 1, source="eval", run_id=run_id)

    response = await workspace.owner.get(
        f"/api/v1/projects/{workspace.project_id}/evaluator-agreement"
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "items": [
            {
                "evaluator_id": str(earlier_id),
                "score_name": "accuracy",
                "status": "not_enough_pairs",
                "pairs": 0,
                "kappa": None,
                "flagged": False,
            },
            {
                "evaluator_id": str(judge_id),
                "score_name": "judge",
                "status": "not_enough_pairs",
                "pairs": 1,
                "kappa": None,
                "flagged": False,
            },
        ]
    }


async def test_agreement_answers_for_a_heuristic_evaluator(
    browser_factory: BrowserFactory, session_factory: Sessions
) -> None:
    """Only the summary is judges-only; the single route measures any kind (api-deviations)."""
    workspace = await create_workspace(await browser_factory("owner@example.com"))
    evaluator_id = await add_evaluator(
        session_factory, workspace.project_id, "length", kind="heuristic"
    )
    run_id = await insert_run(session_factory, evaluator_id, started_at=datetime.now(UTC))
    trace = new_trace_id()
    await add_score(session_factory, workspace.project_id, trace, 1, name="length")
    await add_score(
        session_factory, workspace.project_id, trace, 1, name="length", source="eval", run_id=run_id
    )

    response = await workspace.owner.get(_url(workspace, evaluator_id))

    assert response.status_code == 200, response.text
    assert (response.json()["score_name"], response.json()["pairs"]) == ("length", 1)


async def test_unknown_evaluator_is_404(
    browser_factory: BrowserFactory, session_factory: Sessions
) -> None:
    workspace, _, _ = await judge_world(browser_factory, session_factory)

    response = await workspace.owner.get(_url(workspace, uuid.uuid4()))

    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"


async def test_cross_tenant_evaluator_is_404(
    browser_factory: BrowserFactory, session_factory: Sessions
) -> None:
    """Another organization's judge, asked for under this project's path, is not found."""
    workspace, _, _ = await judge_world(browser_factory, session_factory)
    other = await create_workspace(await browser_factory("rival@example.com"), org_name="Rival")
    foreign_id = await add_evaluator(session_factory, other.project_id, "judge")
    foreign_run = await insert_run(session_factory, foreign_id, started_at=datetime.now(UTC))
    trace = new_trace_id()
    await add_score(session_factory, other.project_id, trace, 1)
    await add_score(session_factory, other.project_id, trace, 1, source="eval", run_id=foreign_run)

    response = await workspace.owner.get(_url(workspace, foreign_id))
    summary = await workspace.owner.get(
        f"/api/v1/projects/{workspace.project_id}/evaluator-agreement"
    )

    assert response.status_code == 404
    assert [item["score_name"] for item in summary.json()["items"]] == ["judge"]
    assert summary.json()["items"][0]["pairs"] == 0


async def test_summary_without_judges_is_empty(browser_factory: BrowserFactory) -> None:
    workspace = await create_workspace(await browser_factory("owner@example.com"))

    response = await workspace.owner.get(
        f"/api/v1/projects/{workspace.project_id}/evaluator-agreement"
    )

    assert (response.status_code, response.json()) == (200, {"items": []})
