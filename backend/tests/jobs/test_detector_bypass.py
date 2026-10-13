"""`run_detectors` lists the active projects with the bypass, then runs each one bound to itself.
Its insights and run records must land in the project whose spans produced them.
"""

from datetime import UTC, datetime, timedelta

from app.config import Settings
from app.insights.job_queries import active_project_ids, open_insights_by_severity
from app.insights.jobs import ACTIVE_WINDOW, detect_all
from app.insights.schemas import Severity
from tests.jobs.bypass_support import (
    SessionFactory,
    fetch,
    new_projects,
    seed_llm_spans,
    seed_row,
    visible_to,
)

# `unpriced_spend` opens an info insight for 20 or more unpriced calls of one model.
_ENOUGH = 20
_INSIGHTS = "SELECT project_id, kind, status, evidence FROM insights"


async def test_active_projects_are_those_with_recent_spans(
    session_factory: SessionFactory,
) -> None:
    now = datetime.now(UTC)
    a, b, idle, stale = await new_projects(session_factory, 4)
    await seed_llm_spans(session_factory, a, 1, started_at=now - timedelta(hours=1))
    await seed_llm_spans(session_factory, b, 1, started_at=now - timedelta(hours=23))
    await seed_llm_spans(session_factory, stale, 1, started_at=now - timedelta(hours=30))

    async with session_factory() as db:
        listed = await active_project_ids(db, now - ACTIVE_WINDOW, now)

    assert set(listed) == {a, b}  # neither the project without spans nor the one with old spans
    assert idle not in listed and stale not in listed


async def test_insights_open_in_the_project_whose_spans_caused_them(
    session_factory: SessionFactory, settings: Settings
) -> None:
    now = datetime.now(UTC)
    a, b, below = await new_projects(session_factory, 3)
    started = now - timedelta(hours=1)
    await seed_llm_spans(session_factory, a, _ENOUGH, started_at=started, model="a-model")
    await seed_llm_spans(session_factory, b, _ENOUGH + 5, started_at=started, model="b-model")
    await seed_llm_spans(session_factory, below, _ENOUGH - 1, started_at=started, model="c-model")

    run = await detect_all(session_factory, settings, now=now)

    assert run.projects == 3 and run.projects_failed == 0 and run.opened == 2
    insights = await fetch(session_factory, _INSIGHTS)
    assert {(row["project_id"], row["kind"], row["status"]) for row in insights} == {
        (a, "unpriced_spend", "open"),
        (b, "unpriced_spend", "open"),
    }
    calls = {row["project_id"]: row["evidence"]["metrics"]["calls"] for row in insights}
    assert calls == {a: _ENOUGH, b: _ENOUGH + 5}
    runs = await fetch(
        session_factory, "SELECT project_id, count(*) AS runs FROM detector_runs GROUP BY 1"
    )
    assert {row["project_id"] for row in runs} == {a, b, below}
    assert len({row["runs"] for row in runs}) == 1  # every project ran the same detectors
    # What the job opened is invisible from the other project.
    seen_by_a = await visible_to(session_factory, a, _INSIGHTS)
    assert [row["project_id"] for row in seen_by_a] == [a]
    seen_by_b = await visible_to(session_factory, b, _INSIGHTS)
    assert [row["project_id"] for row in seen_by_b] == [b]
    # The project below the threshold has run records of its own and no insight.
    own_runs = await visible_to(session_factory, below, "SELECT project_id FROM detector_runs")
    assert own_runs and {row["project_id"] for row in own_runs} == {below}
    assert await visible_to(session_factory, below, _INSIGHTS) == []


async def test_a_pass_leaves_the_insights_of_projects_it_did_not_run(
    session_factory: SessionFactory, settings: Settings
) -> None:
    now = datetime.now(UTC)
    active, dormant = await new_projects(session_factory, 2)
    await seed_llm_spans(session_factory, active, _ENOUGH, started_at=now - timedelta(hours=1))
    # The dormant project has an open insight and no spans: no pass runs it, so it must stay open.
    seeded = await seed_row(session_factory, "insights", dormant)

    await detect_all(session_factory, settings, now=now)

    rows = await fetch(session_factory, "SELECT id, project_id, status, occurrences FROM insights")
    by_project = {row["project_id"]: row for row in rows}
    assert set(by_project) == {active, dormant}
    assert (by_project[dormant]["id"], by_project[dormant]["status"]) == (seeded["id"], "open")
    assert by_project[dormant]["occurrences"] == seeded["occurrences"]
    async with session_factory() as db:
        counts = await open_insights_by_severity(db)
    assert counts[Severity.INFO] == 2  # the gauge counts every project's open insights
