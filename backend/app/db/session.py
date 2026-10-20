"""Async engine and session factory."""

from typing import Literal

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def create_engine(
    database_url: str,
    *,
    pool_size: int = 10,
    max_overflow: int | None = None,
    pool_timeout: float = 30,
    pre_ping: bool = True,
    isolation_level: Literal["AUTOCOMMIT"] | None = None,
    statement_timeout_ms: int | None = None,
) -> AsyncEngine:
    """An async engine.

    By default the pool may double in size under load and a request waits up to 30 seconds for a
    connection. A pool for short work that never nests sets `max_overflow` and `pool_timeout`.
    `pre_ping` tests each connection on checkout; turn it off for a pool whose callers already
    handle a failed statement and cannot afford the extra round trip. `isolation_level`
    `AUTOCOMMIT` is for a pool that only runs single statements: no BEGIN or COMMIT round trips.
    `statement_timeout_ms` cancels any statement on these connections that runs longer.
    """
    options = "-c timezone=UTC"
    if statement_timeout_ms is not None:
        options += f" -c statement_timeout={statement_timeout_ms}"
    return create_async_engine(
        database_url,
        pool_size=pool_size,
        max_overflow=pool_size if max_overflow is None else max_overflow,
        pool_timeout=pool_timeout,
        pool_pre_ping=pre_ping,
        isolation_level=isolation_level,
        # Without this a DBAPIError's text ends in `[parameters: (...)]`, and that text goes to
        # Sentry and to stdout via the exception log. A deadlock or reset while inserting a
        # trace would then send fragments of prompts and completions to both.
        hide_parameters=True,
        connect_args={
            # Pin the session time zone so timestamps and date_trunc() are UTC everywhere. The
            # api's statement cap is a connection default too, so no transaction spends a round
            # trip setting it; `lift_statement_limit`'s SET LOCAL still overrides it.
            "options": options,
            # No server-side prepared statements. psycopg prepares a statement after five runs on
            # a connection, and Postgres may then switch it to a generic plan, which is built
            # without the parameter values. A query over a time window then assumes a small one
            # and picks a join that is far slower for a day of spans: a 1 hour overview got ten
            # times slower. Planning each statement afresh costs a fraction of a millisecond.
            "prepare_threshold": None,
        },
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # expire_on_commit=False lets handlers serialize ORM objects after commit.
    return async_sessionmaker(engine, expire_on_commit=False)
