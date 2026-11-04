"""Ownership guards for disposable test databases, including ambiguous CREATE outcomes."""

import re
import sys
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg import sql


def drop_owned_database(base_url: str, database: str) -> None:
    """Drop one exact UUID identity; never discover databases using a name pattern."""
    if not re.fullmatch(r"spanlight_test_[0-9a-f]{32}_(?:main|gw[0-9]+)", database):
        raise ValueError("Refusing cleanup of a database without an invocation ownership identity")
    with psycopg.connect(base_url, autocommit=True, connect_timeout=10) as connection:
        connection.execute("SET statement_timeout = '10s'")
        connection.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(database))
        )


@contextmanager
def owned_database(base_url: str, database: str) -> Iterator[None]:
    """Cover CREATE itself: a failed acknowledgement can still leave a database behind."""
    try:
        with psycopg.connect(base_url, autocommit=True, connect_timeout=10) as connection:
            connection.execute("SET statement_timeout = '10s'")
            connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
        yield
    finally:
        failure = sys.exception()
        try:
            drop_owned_database(base_url, database)
        except Exception as cleanup_error:
            if failure is None:
                raise
            failure.add_note(f"Owned database {database} cleanup failed: {cleanup_error}")
