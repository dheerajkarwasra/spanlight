"""Migration 0704 adds `insight_feedback`, members' verdicts on Doctor insights, and can be undone.

One verdict per user per insight (a second vote replaces the first in the application), a note of
at most 500 characters, and the vote goes when its insight or its user does.

The round trip downgrades the session's shared database and leaves it at head.
"""

import uuid

import psycopg
import pytest

from app.db.migrations import downgrade_to, head_revision, upgrade_to_head
from tests.db import schema_probe as probe

TABLE = "insight_feedback"
ORG_ID = uuid.UUID("10000000-0000-4000-8000-000000000704")
PROJECT_ID = uuid.UUID("20000000-0000-4000-8000-000000000704")

EXPECTED_COLUMNS = {
    "id": ("uuid", "NO", None, None, None),
    "project_id": ("uuid", "NO", None, None, None),
    "insight_id": ("uuid", "NO", None, None, None),
    "user_id": ("uuid", "NO", None, None, None),
    "verdict": ("text", "NO", None, None, None),
    "note": ("text", "YES", None, None, None),
    "created_at": ("timestamptz", "NO", None, None, "now()"),
    "updated_at": ("timestamptz", "NO", None, None, "now()"),
}
EXPECTED_CONSTRAINTS = {
    "insight_feedback_insight_fkey": (
        "FOREIGN KEY (insight_id, project_id) REFERENCES insights(id, project_id) ON DELETE CASCADE"
    ),
    "insight_feedback_vote_key": "UNIQUE (project_id, insight_id, user_id)",
}


def _assert_at_head(database_url: str) -> None:
    assert probe.current_revision(database_url) == head_revision()
    assert probe.columns(database_url, TABLE) == EXPECTED_COLUMNS
    probe.assert_project_policies(database_url, TABLE)
    constraints = probe.constraints(database_url, TABLE)
    for name, definition in EXPECTED_CONSTRAINTS.items():
        assert constraints[name] == (definition, True), name
    assert {"insight_feedback_verdict_check", "insight_feedback_note_check"} <= constraints.keys()
    assert probe.indexes(database_url, TABLE)["insight_feedback_user_idx"] == (
        f"CREATE INDEX insight_feedback_user_idx ON public.{TABLE} USING btree (user_id)"
    )


def _insight(connection: psycopg.Connection) -> uuid.UUID:
    insight_id = uuid.uuid4()
    connection.execute(
        "INSERT INTO insights (id, project_id, kind, severity, status, fingerprint, title, "
        "summary, failure_layer, certainty, evidence, suggested_fix, verification, "
        "first_seen_at, last_seen_at, occurrences) "
        "VALUES (%s, %s, 'test', 'info', 'open', %s, 'title', 'summary', 'client', 'measured', "
        "'{}', 'fix', 'verify', now(), now(), 1)",
        (insight_id, PROJECT_ID, insight_id.hex),
    )
    return insight_id


def _user(connection: psycopg.Connection) -> uuid.UUID:
    user_id = uuid.uuid4()
    connection.execute(
        "INSERT INTO users (id, email, name) VALUES (%s, %s, 'Voter')",
        (user_id, f"{user_id.hex}@example.com"),
    )
    return user_id


def _vote(
    connection: psycopg.Connection, insight_id: uuid.UUID, user_id: uuid.UUID, note: str | None
) -> None:
    connection.execute(
        f"INSERT INTO {TABLE} (id, project_id, insight_id, user_id, verdict, note) "
        "VALUES (%s, %s, %s, %s, 'right', %s)",
        (uuid.uuid4(), PROJECT_ID, insight_id, user_id, note),
    )


def _votes(connection: psycopg.Connection) -> int:
    row = connection.execute(f"SELECT count(*) FROM {TABLE}").fetchone()
    assert row is not None
    return int(row[0])


def test_0704_round_trip(app_database_url: str) -> None:
    _assert_at_head(app_database_url)
    with probe.admin() as connection:
        probe.seed_project(connection, ORG_ID, PROJECT_ID)
        insight_id, user_id = _insight(connection), _user(connection)
        _vote(connection, insight_id, user_id, "x" * 500)
        with pytest.raises(psycopg.errors.UniqueViolation, match="insight_feedback_vote_key"):
            _vote(connection, insight_id, user_id, None)
        with pytest.raises(psycopg.errors.CheckViolation, match="insight_feedback_note_check"):
            _vote(connection, insight_id, _user(connection), "x" * 501)
        with pytest.raises(psycopg.errors.CheckViolation, match="insight_feedback_verdict_check"):
            connection.execute(f"UPDATE {TABLE} SET verdict = 'maybe'")

        connection.execute("DELETE FROM insights WHERE id = %s", (insight_id,))
        assert _votes(connection) == 0
        _vote(connection, _insight(connection), user_id, None)
        connection.execute("DELETE FROM users WHERE id = %s", (user_id,))
        assert _votes(connection) == 0

    downgrade_to(app_database_url, "0703")
    try:
        assert probe.current_revision(app_database_url) == "0703"
        assert probe.row_security(app_database_url, TABLE) is None
    finally:
        upgrade_to_head(app_database_url)

    _assert_at_head(app_database_url)
