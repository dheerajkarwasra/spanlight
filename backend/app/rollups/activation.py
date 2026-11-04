"""Administrative coverage activation after all uninstrumented writers are drained."""

from datetime import datetime

import psycopg

# Accepted telemetry is at most 90 days old; new projects have no pre-activation source history.
NEW_PROJECT_BOUNDARY = "1970-01-01 00:00:00+00"


class NonemptyInventoryError(ValueError):
    """Fresh activation refused: projects exist, so their history needs a drained activation."""


def activate_aggregate_coverage(database_url: str, *, fresh_install: bool = False) -> datetime:
    """Activate separately from migrations. The operator must first drain every old writer.

    Fresh mode requires an empty project inventory, including projects outside any RLS binding.
    Legacy projects retain a conservative future boundary; only new project IDs start complete.
    """
    url = database_url.replace("postgresql+psycopg://", "postgresql://")
    refusal = "Fresh coverage activation requires an empty project inventory"
    with psycopg.connect(url) as connection:
        # Refuse a live inventory before the lock: Compose runs fresh mode on every `up`, and the
        # lock would queue behind (or time out on) a running api's row locks on projects.
        if fresh_install and _has_projects(connection):
            raise NonemptyInventoryError(refusal)
        # End the check's transaction: upgrading its ACCESS SHARE lock to the exclusive one below
        # deadlocks two activations that both passed the check.
        connection.rollback()
        connection.execute("SET LOCAL lock_timeout = '5s'")
        connection.execute("LOCK TABLE projects IN ACCESS EXCLUSIVE MODE")
        if fresh_install and _has_projects(connection):
            raise NonemptyInventoryError(refusal)
        row = connection.execute("""
            SELECT date_trunc('day', clock_timestamp() + interval '10 minutes', 'UTC')
                   + interval '1 day'
        """).fetchone()
        if row is None:
            raise RuntimeError("Database did not return an activation boundary")
        boundary: datetime = row[0]
        if not fresh_install:
            connection.execute(
                """
                UPDATE projects SET aggregate_history_unverified_before = %s
                WHERE aggregate_history_unverified_before IS NULL
            """,
                (boundary,),
            )
        connection.execute(
            "ALTER TABLE projects ALTER COLUMN aggregate_history_unverified_before "
            f"SET DEFAULT '{NEW_PROJECT_BOUNDARY}'::timestamptz"
        )
    return boundary


def _has_projects(connection: psycopg.Connection[tuple[object, ...]]) -> bool:
    row = connection.execute("SELECT EXISTS (SELECT 1 FROM projects)").fetchone()
    return bool(row and row[0])
