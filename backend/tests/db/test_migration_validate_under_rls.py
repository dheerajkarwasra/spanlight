"""`VALIDATE CONSTRAINT` checks every row even when the migrating role is subject to FORCE RLS.

Failure mode (full test pass 2026-10-13, C-002): migrations run as the table owner, which is
NOSUPERUSER NOBYPASSRLS (as the test role here is), and `FORCE ROW LEVEL SECURITY` applies the
policies to the owner too. Postgres validates a foreign key with a query, and that query sees only
the rows the policies let through; with neither `app.project_id` nor `app.bypass_rls` set it sees
none, so the key was marked valid while dangling ids existed. Each `VALIDATE CONSTRAINT` now runs
with `app.bypass_rls` set for its own transaction.

A CHECK constraint is validated by scanning the table directly, which row-level security does not
filter, so the CHECK cases below pass with or without the bypass; they pin that every row is
checked on the idempotent re-run path of 0400 and 0401 (columns already present from an
interrupted run).

Every test downgrades the session's shared database and must leave it at head.
"""

import uuid
from collections.abc import Iterator

import psycopg
import pytest
from sqlalchemy import create_engine, exc, text

from alembic import command
from app.db.migrations import alembic_config, downgrade_to, head_revision, upgrade_to_head
from tests.conftest import admin_database_url

ORG_ID = uuid.UUID("10000000-0000-4000-8000-00000000c002")
PROJECT_ID = uuid.UUID("20000000-0000-4000-8000-00000000c002")
TRACE_ID = "c002" * 8
SPAN_ID = "c002" * 4
SCORE_ID = uuid.UUID("62000000-0000-4000-8000-00000000c002")
MISSING_RUN_ID = uuid.UUID("61000000-0000-4000-8000-00000000dead")


def _admin(statements: list[tuple[str, tuple[object, ...]]]) -> None:
    """Run statements as the superuser: it sees and writes every row whatever the policies say."""
    libpq_url = admin_database_url().replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(libpq_url, autocommit=True) as connection:
        for sql, params in statements:
            connection.execute(sql, params)  # type: ignore[arg-type]  # literal SQL in tests


def _seed_project() -> None:
    _admin(
        [
            (
                "INSERT INTO organizations (id, name, slug) VALUES (%s, 'Org', 'org-c002')",
                (ORG_ID,),
            ),
            (
                "INSERT INTO projects (id, org_id, name, slug) "
                "VALUES (%s, %s, 'Proj', 'proj-c002')",
                (PROJECT_ID, ORG_ID),
            ),
        ]
    )


def _seed_span(extra_columns: str = "", extra_values: tuple[object, ...] = ()) -> None:
    placeholders = ", %s" * len(extra_values)
    _admin(
        [
            (
                "INSERT INTO traces (project_id, trace_id, started_at, ended_at) "
                "VALUES (%s, %s, now(), now())",
                (PROJECT_ID, TRACE_ID),
            ),
            (
                "INSERT INTO spans (project_id, trace_id, span_id, kind, name, status, "
                f"started_at, ended_at{extra_columns}) "
                f"VALUES (%s, %s, %s, 'llm', 'chat', 'ok', now(), now(){placeholders})",
                (PROJECT_ID, TRACE_ID, SPAN_ID, *extra_values),
            ),
        ]
    )


def _upgrade_to(database_url: str, revision: str) -> None:
    command.upgrade(alembic_config(database_url), revision)


def _current_revision(database_url: str) -> str | None:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            return connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
    finally:
        engine.dispose()


@pytest.fixture
def back_to_head(app_database_url: str) -> Iterator[None]:
    """Remove this module's rows and bring the shared database back to head afterwards."""
    yield
    _admin(
        [
            ("DELETE FROM projects WHERE id = %s", (PROJECT_ID,)),  # cascades to its rows
            ("DELETE FROM organizations WHERE id = %s", (ORG_ID,)),
        ]
    )
    upgrade_to_head(app_database_url)
    assert _current_revision(app_database_url) == head_revision()


def test_0505_refuses_a_score_pointing_at_a_missing_evaluator_run(
    app_database_url: str, back_to_head: None
) -> None:
    downgrade_to(app_database_url, "0504")
    _seed_project()
    _admin(
        [
            (
                "INSERT INTO scores (id, project_id, trace_id, name, data_type, value, source, "
                "evaluator_run_id) VALUES (%s, %s, %s, 'len', 'numeric', 0.8, 'eval', %s)",
                (SCORE_ID, PROJECT_ID, TRACE_ID, MISSING_RUN_ID),
            )
        ]
    )

    with pytest.raises(exc.IntegrityError) as raised:
        _upgrade_to(app_database_url, "0505")
    assert isinstance(raised.value.orig, psycopg.errors.ForeignKeyViolation)
    assert _current_revision(app_database_url) == "0504"

    # Without the dangling id the same upgrade goes through.
    _admin([("UPDATE scores SET evaluator_run_id = NULL WHERE id = %s", (SCORE_ID,))])
    _upgrade_to(app_database_url, "0505")
    assert _current_revision(app_database_url) == "0505"


def test_0400_rerun_refuses_an_error_class_on_a_span_that_did_not_fail(
    app_database_url: str, back_to_head: None
) -> None:
    downgrade_to(app_database_url, "0307")
    _seed_project()
    # An interrupted earlier run left the column behind, and a row was written into it.
    _admin([("ALTER TABLE spans ADD COLUMN error_class text", ())])
    _seed_span(", error_class", ("timeout",))

    with pytest.raises(exc.IntegrityError) as raised:
        _upgrade_to(app_database_url, "0400")
    assert isinstance(raised.value.orig, psycopg.errors.CheckViolation)

    _admin([("UPDATE spans SET error_class = NULL WHERE span_id = %s", (SPAN_ID,))])
    _upgrade_to(app_database_url, "0400")
    assert _current_revision(app_database_url) == "0400"


def test_0401_rerun_refuses_a_malformed_request_hash(
    app_database_url: str, back_to_head: None
) -> None:
    downgrade_to(app_database_url, "0400")
    _seed_project()
    _admin(
        [
            (
                "ALTER TABLE spans ADD COLUMN request_hash text, ADD COLUMN finish_reason text",
                (),
            )
        ]
    )
    _seed_span(", request_hash", ("NOT-A-HASH",))

    with pytest.raises(exc.IntegrityError) as raised:
        _upgrade_to(app_database_url, "0401")
    assert isinstance(raised.value.orig, psycopg.errors.CheckViolation)

    _admin([("UPDATE spans SET request_hash = NULL WHERE span_id = %s", (SPAN_ID,))])
    _upgrade_to(app_database_url, "0401")
    assert _current_revision(app_database_url) == "0401"
