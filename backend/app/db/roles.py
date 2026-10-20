"""Provision the database role the application connects as.

Row-level security does not apply to superusers or BYPASSRLS roles, so the
API and worker must connect as an ordinary role. Migrations run as the database
owner; this step then creates (or updates) the app role and grants it data
access. It is idempotent and safe to run on every deploy.
"""

import re
from typing import Any

import psycopg
from psycopg import sql

_ROLE_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")

# Functions the migrations revoke from PUBLIC because only the app may call them, by signature.
# Each is granted to the app role by name, so a function added later stays revoked until it is
# listed here.
APP_FUNCTIONS = ("public.explain_month_to_date_cost(uuid, timestamptz)",)


def ensure_app_role(admin_url: str, role: str, password: str) -> None:
    """Create or update `role` as a login role without RLS bypass, then grant access."""
    if not _ROLE_NAME.fullmatch(role):
        raise ValueError(f"Unsafe role name: {role!r}")

    conninfo = admin_url.replace("postgresql+psycopg://", "postgresql://", 1)
    role_id = sql.Identifier(role)

    with psycopg.connect(conninfo, autocommit=True) as connection:
        exists = connection.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
        verb = sql.SQL("ALTER") if exists else sql.SQL("CREATE")
        # Passwords cannot be bound as parameters in DDL; sql.Literal quotes safely.
        connection.execute(
            sql.SQL(
                "{verb} ROLE {role} LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE "
                "PASSWORD {password}"
            ).format(verb=verb, role=role_id, password=sql.Literal(password))
        )

        database = connection.info.dbname
        for statement in (
            "GRANT CONNECT ON DATABASE {database} TO {role}",
            "GRANT USAGE ON SCHEMA public TO {role}",
            "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {role}",
            "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {role}",
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
            "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {role}",
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {role}",
            # The audit log is append-only (migration 1013's trigger); the app never edits a row.
            # It keeps DELETE for the retention purge, which the trigger lets through.
            "REVOKE UPDATE ON audit_events FROM {role}",
        ):
            connection.execute(
                sql.SQL(statement).format(database=sql.Identifier(database), role=role_id)
            )
        _grant_app_functions(connection, role_id)
        _mark_search_leakproof(connection)


def _grant_app_functions(connection: psycopg.Connection[Any], role_id: sql.Identifier) -> None:
    """Let the role call each function in `APP_FUNCTIONS` that the schema has.

    A database restored from before the function's migration does not have it yet; the
    `ensure-app-role` that follows the next `migrate` grants it.
    """
    for signature in APP_FUNCTIONS:
        exists = connection.execute("SELECT to_regprocedure(%s)", (signature,)).fetchone()
        if exists is not None and exists[0] is not None:
            connection.execute(
                sql.SQL("GRANT EXECUTE ON FUNCTION {function} TO {role}").format(
                    function=sql.SQL(signature), role=role_id
                )
            )


def _mark_search_leakproof(connection: psycopg.Connection[Any]) -> None:
    """Mark `texticlike` LEAKPROOF again, so content search can use its index under RLS.

    Migration 1007 sets the mark and says why it is safe; `pg_dump` does not keep it on a catalog
    function, so a restored database loses it, and this step (which runs after every restore and
    deploy) restores it. Only a superuser can set it; anyone else leaves search correct but slow.
    """
    row = connection.execute(
        "SELECT current_setting('is_superuser') = 'on', "
        "to_regprocedure('spanlight_search_text(jsonb, jsonb)') IS NOT NULL"
    ).fetchone()
    if row is not None and row[0] and row[1]:
        connection.execute("ALTER FUNCTION pg_catalog.texticlike(text, text) LEAKPROOF")
