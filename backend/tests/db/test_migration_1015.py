"""Migration 1015 re-derives the per-user daily stats per environment, and folds them back.

Failure mode: the backfill rewrites every stored day that still has traces, and the refresh job
never recomputes days older than its eight-day reach, so a wrong figure here would stay on the
Users page for good. The test seeds rows at the 1014 shape over traces in three environments
(one of them none), upgrades, and checks one row per environment with the refresh job's figures
and the same totals; a day whose traces are gone keeps its row; the downgrade sums them back.

Every test here leaves the session's shared database at head.
"""

import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from app.db.migrations import downgrade_to, head_revision, upgrade_to_head
from app.db.rls import bypass_rls
from app.end_users.refresh import refresh_user_stats
from tests.conftest import admin_database_url
from tests.db import schema_probe as probe
from tests.jobs.bypass_support import SessionFactory

BELOW = "1014"
ORG_ID = uuid.UUID("10000000-0000-4000-8000-000000001015")
PROJECT_ID = uuid.UUID("20000000-0000-4000-8000-000000001015")
DAY = date(2026, 9, 10)
GONE_DAY = date(2026, 9, 1)  # a day whose traces retention already removed
USER = "u1"
COST = Decimal("0.010000")

# (trace, environment, spans as (status, cost_usd, usage_rollup)). Tokens are 10 + 5 per span.
TRACES: list[tuple[str, str | None, list[tuple[str, Decimal | None, bool]]]] = [
    ("a" * 32, "prod", [("ok", COST, False)]),
    ("b" * 32, "prod", [("ok", COST, False), ("ok", COST, True)]),
    ("c" * 32, "staging", [("error", None, False)]),
    ("d" * 32, None, []),
]

# (environment, traces, llm_calls, errors, unpriced_calls, cost_usd, tokens)
FIGURES = "environment, traces, llm_calls, errors, unpriced_calls, cost_usd, tokens"
PER_ENVIRONMENT = [
    ("prod", 2, 3, 0, 0, Decimal("0.020000"), 30),
    ("staging", 1, 1, 1, 1, None, 15),
    (None, 1, 0, 0, 0, None, 0),
]
FOLDED = (4, 4, 1, 1, Decimal("0.020000"), 45)


@pytest.fixture
def back_to_head(app_database_url: str) -> Iterator[None]:
    yield
    upgrade_to_head(app_database_url)
    assert probe.current_revision(app_database_url) == head_revision()


def _seed_at_1014() -> None:
    """Traces and spans of `DAY`, and stale 1014-shape rows for `DAY` and `GONE_DAY`."""
    start = datetime.combine(DAY, datetime.min.time(), UTC) + timedelta(hours=9)
    with probe.admin() as connection:
        probe.seed_project(connection, ORG_ID, PROJECT_ID)
        for trace_id, environment, spans in TRACES:
            connection.execute(
                "INSERT INTO traces (project_id, trace_id, started_at, ended_at, "
                "external_user_id, environment) VALUES (%s, %s, %s, %s, %s, %s)",
                (PROJECT_ID, trace_id, start, start + timedelta(seconds=1), USER, environment),
            )
            for index, (status, cost, rollup) in enumerate(spans):
                connection.execute(
                    "INSERT INTO spans (project_id, trace_id, span_id, kind, name, status, "
                    "started_at, ended_at, provider, model, input_tokens, output_tokens, "
                    "cost_usd, usage_rollup) VALUES "
                    "(%s, %s, %s, 'llm', 'call', %s, %s, %s, 'openai', 'm', 10, 5, %s, %s)",
                    (
                        PROJECT_ID,
                        trace_id,
                        f"{trace_id[:15]}{index}",
                        status,
                        start,
                        start + timedelta(seconds=1),
                        cost,
                        rollup,
                    ),
                )
        for day in (DAY, GONE_DAY):
            connection.execute(
                "INSERT INTO user_stats_daily (project_id, day, external_user_id, traces, "
                "llm_calls, errors, unpriced_calls, cost_usd, tokens, computed_at) "
                "VALUES (%s, %s, %s, 99, 99, 0, 0, 9.99, 999, now())",
                (PROJECT_ID, day, USER),
            )


def _rows(day: date) -> list[tuple[object, ...]]:
    """The day's rows, read as the superuser: the app role's reads are bound to a project."""
    return probe.query(
        admin_database_url(),
        f"SELECT {FIGURES} FROM user_stats_daily "
        "WHERE project_id = :project_id AND day = :day ORDER BY environment NULLS LAST",
        project_id=PROJECT_ID,
        day=day,
    )


async def test_1015_rederives_per_environment_and_folds_back(
    app_database_url: str, back_to_head: None, session_factory: SessionFactory
) -> None:
    downgrade_to(app_database_url, BELOW)
    _seed_at_1014()

    upgrade_to_head(app_database_url)
    assert _rows(DAY) == PER_ENVIRONMENT
    # Retention took this day's traces; its row stays, under no environment.
    assert _rows(GONE_DAY) == [(None, 99, 99, 0, 0, Decimal("9.990000"), 999)]

    # The refresh job writes the same figures over the same traces.
    async with session_factory() as db:
        await bypass_rls(db)
        await refresh_user_stats(db, PROJECT_ID, [DAY])
        await db.commit()
    assert _rows(DAY) == PER_ENVIRONMENT

    downgrade_to(app_database_url, BELOW)
    folded = probe.query(
        admin_database_url(),
        "SELECT traces, llm_calls, errors, unpriced_calls, cost_usd, tokens FROM user_stats_daily "
        "WHERE project_id = :project_id AND day = :day",
        project_id=PROJECT_ID,
        day=DAY,
    )
    assert folded == [FOLDED]
