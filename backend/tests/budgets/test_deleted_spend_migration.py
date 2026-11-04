"""Migration 1031 in an isolated database: the ledger table, its policies and the marker."""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from urllib.parse import urlsplit, urlunsplit

import psycopg

from alembic import command
from app.db.migrations import alembic_config, downgrade_to
from tests.conftest import _base_database_url, _libpq
from tests.database_lifecycle import owned_database
from tests.db import schema_probe as probe

TRACE = "f" * 32


@contextmanager
def _database_at(revision: str) -> Iterator[str]:
    name = f"spanlight_test_{uuid.uuid4().hex}_main"
    base = _libpq(_base_database_url())
    url = urlunsplit(urlsplit(base)._replace(path=f"/{name}"))
    with owned_database(base, name):
        command.upgrade(alembic_config(url), revision)
        yield url


def _seed_project(db: psycopg.Connection, retention_days: int) -> tuple[uuid.UUID, uuid.UUID]:
    org_id, project_id = uuid.uuid4(), uuid.uuid4()
    probe.seed_project(db, org_id, project_id)
    db.execute(
        "UPDATE projects SET retention_days = %s WHERE id = %s", (retention_days, project_id)
    )
    return org_id, project_id


def test_upgrade_marks_only_projects_with_evidence(database_server: None) -> None:
    with _database_at("1030") as url:
        with psycopg.connect(url) as db:
            _, project_a = _seed_project(db, 90)
            _, project_b = _seed_project(db, 1)
            org_c, project_c = _seed_project(db, 90)
            org_d, project_d = _seed_project(db, 90)
            org_e, project_e = _seed_project(db, 90)
            db.execute(
                "INSERT INTO audit_events (id, org_id, action, target_type, target_id, "
                "project_id, metadata) VALUES "
                "(%s, %s, 'end_user.erase', 'end_user', 'hash', %s, '{}'), "
                """(%s, %s, 'project.update', 'project', %s, %s, """
                """'{"changes": {"retention_days": 7}}'), """
                """(%s, %s, 'project.update', 'project', %s, %s, '{"changes": {"name": "x"}}')""",
                (
                    uuid.uuid4(),
                    org_c,
                    project_c,
                    uuid.uuid4(),
                    org_d,
                    str(project_d),
                    project_d,
                    uuid.uuid4(),
                    org_e,
                    str(project_e),
                    project_e,
                ),
            )
        command.upgrade(alembic_config(url), "1031")
        with psycopg.connect(url) as db:
            clock = db.execute("SELECT now(), date_trunc('month', now(), 'UTC')").fetchone()
            assert clock is not None
            now, month = clock
            markers: dict[uuid.UUID, datetime | None] = {
                project_id: marker
                for project_id, marker in db.execute(
                    "SELECT id, budget_history_missing_through FROM projects"
                )
            }
    assert markers[project_a] is None
    marker_b = markers[project_b]
    if now - timedelta(days=1) > month:
        assert marker_b is not None
        assert abs(marker_b - (now - timedelta(days=1))) < timedelta(seconds=5)
    else:
        assert marker_b is None  # first of the month: a one-day retention reaches no further
    for marked in (project_c, project_d):
        marker = markers[marked]
        assert marker is not None
        assert abs(marker - now) < timedelta(seconds=5)
    assert markers[project_e] is None


def test_ledger_table_policies(database_server: None) -> None:
    with _database_at("1031") as url:
        probe.assert_project_policies(url, "budget_deleted_spans")
        assert probe.columns(url, "budget_deleted_spans")["cost_usd"][:4] == (
            "numeric",
            "NO",
            18,
            8,
        )


def test_downgrade_restores_1026_function(database_server: None) -> None:
    with _database_at("1031") as url:
        downgrade_to(url, "1030")
        assert probe.columns(url, "budget_deleted_spans") == {}
        with psycopg.connect(url) as db:
            _, project = _seed_project(db, 90)
            db.execute(
                "INSERT INTO traces (project_id, trace_id, started_at, ended_at) "
                "VALUES (%s, %s, now(), now())",
                (project, TRACE),
            )
            db.execute(
                "INSERT INTO spans (project_id, trace_id, span_id, kind, name, status, "
                "started_at, ended_at, cost_usd) "
                "VALUES (%s, %s, '0123456789abcdef', 'llm', 'call', 'ok', now(), now(), 0.5)",
                (project, TRACE),
            )
            db.execute("DELETE FROM spans WHERE project_id = %s", (project,))
            assert db.execute(
                "SELECT budget_history_missing_through IS NOT NULL FROM projects WHERE id = %s",
                (project,),
            ).fetchone() == (True,)
