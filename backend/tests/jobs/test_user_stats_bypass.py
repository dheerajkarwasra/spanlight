"""`refresh_user_stats` lists the projects with user-tagged traces with the bypass, then refreshes
each one bound to itself. Each project's per-user rows must count that project's traces only,
even when two projects name the same end user.
"""

from datetime import UTC, datetime, timedelta

from app.end_users.jobs import refresh_all
from tests.jobs.bypass_support import (
    SessionFactory,
    fetch,
    new_projects,
    seed_llm_spans,
    seed_row,
    visible_to,
)

_STATS = (
    "SELECT project_id, external_user_id, sum(traces) AS traces, sum(llm_calls) AS calls "
    "FROM user_stats_daily GROUP BY 1, 2"
)


async def test_user_stats_are_counted_per_project_even_for_the_same_user_id(
    session_factory: SessionFactory,
) -> None:
    now = datetime.now(UTC)
    started = now - timedelta(minutes=5)
    a, b, no_users = await new_projects(session_factory, 3)
    await seed_llm_spans(session_factory, a, 2, started_at=started, user_id="shared")
    await seed_llm_spans(session_factory, a, 1, started_at=started, user_id="only-a")
    await seed_llm_spans(session_factory, b, 4, started_at=started, user_id="shared")
    await seed_llm_spans(session_factory, no_users, 3, started_at=started)

    run = await refresh_all(session_factory, now=now)

    assert run.projects == 2 and run.projects_failed == 0  # traces without a user make no project
    stats = {
        (row["project_id"], row["external_user_id"]): (row["traces"], row["calls"])
        for row in await fetch(session_factory, _STATS)
    }
    assert stats == {(a, "shared"): (2, 2), (a, "only-a"): (1, 1), (b, "shared"): (4, 4)}


async def test_a_project_cannot_read_the_user_stats_written_for_another(
    session_factory: SessionFactory,
) -> None:
    now = datetime.now(UTC)
    a, b = await new_projects(session_factory, 2)
    await seed_llm_spans(
        session_factory, a, 1, started_at=now - timedelta(minutes=1), user_id="user-a"
    )
    await seed_llm_spans(
        session_factory, b, 1, started_at=now - timedelta(minutes=1), user_id="user-b"
    )
    await refresh_all(session_factory, now=now)

    seen_by_a = await visible_to(session_factory, a, _STATS)
    assert [(row["project_id"], row["external_user_id"]) for row in seen_by_a] == [(a, "user-a")]
    seen_by_b = await visible_to(session_factory, b, _STATS)
    assert [(row["project_id"], row["external_user_id"]) for row in seen_by_b] == [(b, "user-b")]


async def test_a_refresh_does_not_touch_the_rows_of_a_project_it_did_not_run(
    session_factory: SessionFactory,
) -> None:
    now = datetime.now(UTC)
    active, dormant = await new_projects(session_factory, 2)
    await seed_llm_spans(
        session_factory, active, 1, started_at=now - timedelta(minutes=1), user_id="user"
    )
    # The dormant project has stats and no traces; the refresh would delete them if it ran there.
    kept = await seed_row(session_factory, "user_stats_daily", dormant)

    await refresh_all(session_factory, now=now)

    rows = await fetch(
        session_factory,
        "SELECT external_user_id, traces FROM user_stats_daily WHERE project_id = :project_id",
        {"project_id": dormant},
    )
    assert [(row["external_user_id"], row["traces"]) for row in rows] == [
        (kept["external_user_id"], kept["traces"])
    ]
