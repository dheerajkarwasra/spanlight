"""Catalog reads and superuser writes shared by the migration tests of the 0700 block.

Each helper opens its own short-lived connection, so a test can call it between a downgrade and
an upgrade without holding a lock that the migration would wait on.
"""

import uuid

import psycopg
from sqlalchemy import create_engine, text

from tests.conftest import admin_database_url


def query(database_url: str, sql: str, **params: object) -> list[tuple[object, ...]]:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            return [tuple(row) for row in connection.execute(text(sql), params)]
    finally:
        engine.dispose()


def current_revision(database_url: str) -> object:
    return query(database_url, "SELECT version_num FROM alembic_version")[0][0]


def columns(database_url: str, table: str) -> dict[str, tuple[object, ...]]:
    """column -> (udt_name, is_nullable, numeric_precision, numeric_scale, column_default)."""
    rows = query(
        database_url,
        "SELECT column_name, udt_name, is_nullable, numeric_precision, numeric_scale, "
        "column_default FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = :table",
        table=table,
    )
    return {str(row[0]): row[1:] for row in rows}


def constraints(database_url: str, table: str) -> dict[str, tuple[str, bool]]:
    """constraint name -> (definition, validated) for every constraint of `table`."""
    rows = query(
        database_url,
        "SELECT conname, pg_get_constraintdef(oid), convalidated FROM pg_constraint "
        "WHERE conrelid = to_regclass(:table)",
        table=table,
    )
    return {str(row[0]): (str(row[1]), bool(row[2])) for row in rows}


def policies(database_url: str, table: str) -> dict[str, tuple[object, object]]:
    rows = query(
        database_url,
        "SELECT policyname, qual, with_check FROM pg_policies "
        "WHERE schemaname = 'public' AND tablename = :table",
        table=table,
    )
    return {str(row[0]): (row[1], row[2]) for row in rows}


def indexes(database_url: str, table: str) -> dict[str, str]:
    rows = query(
        database_url,
        "SELECT indexname, indexdef FROM pg_indexes "
        "WHERE schemaname = 'public' AND tablename = :table",
        table=table,
    )
    return {str(row[0]): str(row[1]) for row in rows}


def row_security(database_url: str, table: str) -> tuple[object, ...] | None:
    """(relrowsecurity, relforcerowsecurity), or None when the table does not exist."""
    rows = query(
        database_url,
        "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE oid = to_regclass(:table)",
        table=table,
    )
    return rows[0] if rows else None


def assert_project_policies(database_url: str, table: str) -> None:
    """`table` has FORCE RLS and exactly the two policies of every project table (0505's form)."""
    assert row_security(database_url, table) == (True, True)
    evaluators = policies(database_url, "evaluators")
    assert policies(database_url, table) == {
        f"{table}_project_isolation": evaluators["evaluators_project_isolation"],
        f"{table}_worker_bypass": evaluators["evaluators_worker_bypass"],
    }


def admin() -> psycopg.Connection:
    """The superuser sees and writes every row whatever the policies say."""
    libpq_url = admin_database_url().replace("postgresql+psycopg://", "postgresql://")
    return psycopg.connect(libpq_url, autocommit=True)


def seed_project(connection: psycopg.Connection, org_id: uuid.UUID, project_id: uuid.UUID) -> None:
    connection.execute(
        "INSERT INTO organizations (id, name, slug) VALUES (%s, 'Org', %s)",
        (org_id, f"org-{org_id.hex[-12:]}"),
    )
    connection.execute(
        "INSERT INTO projects (id, org_id, name, slug) VALUES (%s, %s, 'Proj', 'proj')",
        (project_id, org_id),
    )
