"""Migration 1016 lets a trace's token totals be unknown, and puts the zeros back on downgrade.

Failure mode: a stored 0 that stands for "no call reported usage" keeps showing "0 tokens" for
traces no batch touches again, or the backfill nulls a total some span did report. The test seeds
traces at the 1015 shape, upgrades, and checks that only unreported totals became NULL (usage
rollups do not count as reporting, as in the recompute); the downgrade restores 0 and NOT NULL.

Every test here leaves the session's shared database at head.
"""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest

from app.db.migrations import downgrade_to, head_revision, upgrade_to_head
from tests.conftest import admin_database_url
from tests.db import schema_probe as probe

BELOW = "1015"
ORG_ID = uuid.UUID("10000000-0000-4000-8000-000000001016")
PROJECT_ID = uuid.UUID("20000000-0000-4000-8000-000000001016")

# (trace, stored totals at 1015, spans as (input, output, usage_rollup))
TRACES: list[tuple[str, tuple[int, int], list[tuple[int | None, int | None, bool]]]] = [
    ("a" * 32, (10, 5), [(10, 5, False)]),
    ("b" * 32, (0, 0), [(None, None, False)]),  # a failed call: no usage
    ("c" * 32, (7, 0), [(7, None, False)]),  # input reported, output not
    ("d" * 32, (0, 0), [(10, 5, True)]),  # only a usage rollup, never summed
    ("e" * 32, (0, 0), [(0, 0, False)]),  # a reported zero stays zero
]
UPGRADED = [(10, 5), (None, None), (7, None), (None, None), (0, 0)]
DOWNGRADED = [(10, 5), (0, 0), (7, 0), (0, 0), (0, 0)]


@pytest.fixture
def back_to_head(app_database_url: str) -> Iterator[None]:
    yield
    upgrade_to_head(app_database_url)
    assert probe.current_revision(app_database_url) == head_revision()


def _seed_at_1015() -> None:
    start = datetime(2026, 10, 1, 9, tzinfo=UTC)
    with probe.admin() as connection:
        probe.seed_project(connection, ORG_ID, PROJECT_ID)
        for trace_id, (input_total, output_total), spans in TRACES:
            connection.execute(
                "INSERT INTO traces (project_id, trace_id, started_at, ended_at, input_tokens, "
                "output_tokens) VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    PROJECT_ID,
                    trace_id,
                    start,
                    start + timedelta(seconds=1),
                    input_total,
                    output_total,
                ),
            )
            for index, (input_tokens, output_tokens, rollup) in enumerate(spans):
                connection.execute(
                    "INSERT INTO spans (project_id, trace_id, span_id, kind, name, status, "
                    "started_at, ended_at, provider, model, input_tokens, output_tokens, "
                    "usage_rollup) VALUES "
                    "(%s, %s, %s, 'llm', 'call', 'ok', %s, %s, 'openai', 'm', %s, %s, %s)",
                    (
                        PROJECT_ID,
                        trace_id,
                        f"{trace_id[:15]}{index}",
                        start,
                        start + timedelta(seconds=1),
                        input_tokens,
                        output_tokens,
                        rollup,
                    ),
                )


def _totals() -> list[tuple[object, ...]]:
    return probe.query(
        admin_database_url(),
        "SELECT input_tokens, output_tokens FROM traces WHERE project_id = :project_id "
        "ORDER BY trace_id",
        project_id=PROJECT_ID,
    )


def _nullable(database_url: str) -> set[object]:
    columns = probe.columns(database_url, "traces")
    return {columns[name][1] for name in ("input_tokens", "output_tokens")}


def test_1016_nulls_only_unreported_totals_and_restores_zeros(
    app_database_url: str, back_to_head: None
) -> None:
    downgrade_to(app_database_url, BELOW)
    _seed_at_1015()

    upgrade_to_head(app_database_url)
    assert _totals() == UPGRADED
    assert _nullable(app_database_url) == {"YES"}

    downgrade_to(app_database_url, BELOW)
    assert _totals() == DOWNGRADED
    assert _nullable(app_database_url) == {"NO"}
