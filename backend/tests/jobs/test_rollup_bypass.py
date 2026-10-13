"""`rollup_hourly` reads every project with the bypass and must write each project's rollups to
that project alone: its own counts, nothing from a neighbour, nothing for a project without spans.
"""

from datetime import UTC, datetime, timedelta

from app.rollups.jobs import roll_up_all
from app.scores.repair import REPAIR_WINDOW, repair_has_scores
from tests.jobs.bypass_support import (
    SessionFactory,
    execute,
    fetch,
    new_projects,
    seed_llm_spans,
    seed_row,
    visible_to,
)

_SPAN_COUNTS = "SELECT project_id, sum(span_count) AS spans FROM span_rollups_hourly GROUP BY 1"
_TRACE_COUNTS = "SELECT project_id, sum(traces) AS traces FROM trace_rollups_hourly GROUP BY 1"


async def test_rollups_land_under_the_project_that_owns_the_spans(
    session_factory: SessionFactory,
) -> None:
    now = datetime.now(UTC)
    a, b, quiet = await new_projects(session_factory, 3)
    await seed_llm_spans(session_factory, a, 3, started_at=now - timedelta(hours=1), model="a-1")
    await seed_llm_spans(
        session_factory, b, 4, started_at=now - timedelta(hours=1), model="b-1", status="error"
    )
    await seed_llm_spans(session_factory, b, 1, started_at=now - timedelta(hours=2), model="b-2")
    # A rollup row the quiet project already has; the job must leave it alone.
    quiet_row = await seed_row(session_factory, "span_rollups_hourly", quiet)

    projects, _ = await roll_up_all(session_factory, now=now)

    assert projects == 2  # the quiet project has no spans in the window
    spans = {row["project_id"]: row["spans"] for row in await fetch(session_factory, _SPAN_COUNTS)}
    assert spans == {a: 3, b: 5, quiet: 1}
    quiet_rows = await fetch(
        session_factory,
        "SELECT * FROM span_rollups_hourly WHERE project_id = :project_id",
        {"project_id": quiet},
    )
    assert quiet_rows == [quiet_row]
    traces = {
        row["project_id"]: row["traces"] for row in await fetch(session_factory, _TRACE_COUNTS)
    }
    assert traces == {a: 3, b: 5}
    by_model = await fetch(
        session_factory,
        "SELECT project_id, model, sum(span_count) AS spans, sum(errors) AS errors "
        "FROM span_rollups_hourly WHERE model IS NOT NULL GROUP BY 1, 2",
    )
    assert {(row["project_id"], row["model"], row["spans"], row["errors"]) for row in by_model} == {
        (a, "a-1", 3, 0),
        (b, "b-1", 4, 4),
        (b, "b-2", 1, 0),
    }


async def test_a_project_cannot_read_the_rollups_the_job_wrote_for_another(
    session_factory: SessionFactory,
) -> None:
    now = datetime.now(UTC)
    a, b = await new_projects(session_factory, 2)
    await seed_llm_spans(session_factory, a, 2, started_at=now - timedelta(hours=1))
    await seed_llm_spans(session_factory, b, 6, started_at=now - timedelta(hours=1))
    await roll_up_all(session_factory, now=now)

    for table in ("span_rollups_hourly", "trace_rollups_hourly"):
        assert (
            await visible_to(
                session_factory, a, f"SELECT 1 FROM {table} WHERE project_id = :p", {"p": b}
            )
            == []
        )
        assert (
            await visible_to(
                session_factory, b, f"SELECT 1 FROM {table} WHERE project_id = :p", {"p": a}
            )
            == []
        )
    own = await visible_to(
        session_factory, a, "SELECT DISTINCT project_id FROM span_rollups_hourly"
    )
    assert [row["project_id"] for row in own] == [a]
    own = await visible_to(
        session_factory, b, "SELECT sum(span_count) AS spans FROM span_rollups_hourly"
    )
    assert own[0]["spans"] == 6


async def test_backfill_fills_a_gap_for_the_project_that_has_it_only(
    session_factory: SessionFactory,
) -> None:
    now = datetime.now(UTC)
    old = now - timedelta(days=5)
    gap, recent, expired = await new_projects(session_factory, 3)
    await seed_llm_spans(session_factory, gap, 2, started_at=old)
    await seed_llm_spans(session_factory, recent, 4, started_at=now - timedelta(hours=1))
    # Spans older than the project's retention are not worth rolling up.
    await seed_llm_spans(session_factory, expired, 3, started_at=old)
    await execute(
        session_factory, "UPDATE projects SET retention_days = 1 WHERE id = :p", {"p": expired}
    )

    _, backfilled = await roll_up_all(session_factory, now=now)

    assert backfilled > 0
    rows = await fetch(
        session_factory,
        "SELECT project_id, min(bucket_start) AS first, sum(span_count) AS spans "
        "FROM span_rollups_hourly GROUP BY 1",
    )
    by_project = {row["project_id"]: row for row in rows}
    assert set(by_project) == {gap, recent}  # nothing for the project past its retention
    assert by_project[gap]["spans"] == 2
    assert by_project[gap]["first"] <= old
    assert by_project[recent]["spans"] == 4
    assert by_project[recent]["first"] > now - timedelta(hours=3)


_ADD_SCORE = (
    "INSERT INTO scores (id, project_id, trace_id, name, data_type, source, value, created_at) "
    "VALUES (gen_random_uuid(), :project_id, :trace_id, 'quality', 'boolean', 'api', 1, :at)"
)
_FLAGS = "SELECT project_id, trace_id, has_scores FROM traces"


async def test_has_scores_repair_flips_only_recently_scored_traces_in_their_own_project(
    session_factory: SessionFactory,
) -> None:
    now = datetime.now(UTC)
    a, b = await new_projects(session_factory, 2)
    for project in (a, b):
        await seed_llm_spans(session_factory, project, 3, started_at=now - timedelta(hours=1))
    traces = {
        project: [
            row["trace_id"]
            for row in await fetch(
                session_factory,
                "SELECT trace_id FROM traces WHERE project_id = :p ORDER BY trace_id",
                {"p": project},
            )
        ]
        for project in (a, b)
    }
    recent, old = now - timedelta(minutes=1), now - REPAIR_WINDOW - timedelta(minutes=1)
    # Per project: the first trace has a recent score, the second only an old one, the third none.
    for project, (scored, stale, _) in traces.items():
        for trace_id, at in ((scored, recent), (stale, old)):
            await execute(
                session_factory, _ADD_SCORE, {"project_id": project, "trace_id": trace_id, "at": at}
            )
    # A score in A that names a trace of B (no such trace in A) must not flag B's trace.
    other_trace = traces[b][2]
    await execute(
        session_factory, _ADD_SCORE, {"project_id": a, "trace_id": other_trace, "at": recent}
    )

    async with session_factory() as db:
        repaired = await repair_has_scores(db, now)
        await db.commit()

    flagged = {
        (row["project_id"], row["trace_id"])
        for row in await fetch(session_factory, _FLAGS)
        if row["has_scores"]
    }
    assert repaired == 2
    assert flagged == {(a, traces[a][0]), (b, traces[b][0])}
