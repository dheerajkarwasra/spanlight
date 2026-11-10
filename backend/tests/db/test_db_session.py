"""The engine must not echo bound parameters into database error messages."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.session import create_engine

# Built in two parts so the text is never a literal near the failing call: the traceback of a
# test failure prints source lines.
CUSTOMER_TEXT = "ada@example.com asks why " + "her invoice was charged twice"


async def test_database_errors_do_not_contain_bound_parameters(engine: AsyncEngine) -> None:
    """A deadlock or reset during a /v1/traces insert must not put a prompt in an error string.

    SQLAlchemy appends `[parameters: ...]` to the text of a DBAPIError, and Sentry and the
    structured logs both ship that text. Division by zero is used because, unlike a cast or a
    constraint error, Postgres does not echo the offending value in its own message, so only
    SQLAlchemy can put the parameter there.
    """
    async with engine.connect() as connection:
        with pytest.raises(DBAPIError) as raised:
            await connection.execute(
                text("SELECT CAST(:content AS text), 1 / 0"), {"content": CUSTOMER_TEXT}
            )
    assert "division by zero" in str(raised.value)
    assert CUSTOMER_TEXT not in str(raised.value)


async def test_a_pool_without_overflow_gives_up_after_its_timeout(app_database_url: str) -> None:
    """The pool that idempotency keys use must fail fast instead of growing or waiting 30 s."""
    engine = create_engine(app_database_url, pool_size=1, max_overflow=0, pool_timeout=0.2)
    try:
        async with engine.connect():
            with pytest.raises(PoolTimeoutError):
                async with engine.connect():
                    pass  # pragma: no cover
    finally:
        await engine.dispose()


async def test_connections_bound_the_connect_and_probe_a_silent_peer(engine: AsyncEngine) -> None:
    """psycopg waits 130 s for a connect by default, and a dead peer hangs a query for minutes."""
    async with engine.connect() as connection:
        raw = await connection.get_raw_connection()
        assert raw.driver_connection is not None
        parameters = raw.driver_connection.info.get_parameters()
    assert parameters["connect_timeout"] == "5"
    assert parameters["keepalives_idle"] == "30"
