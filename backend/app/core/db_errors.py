"""What a database error means: the client's mistake, an overload, or worth another try. Pure.

The API's error handlers (`app.core.errors`), the gateway's error handlers and `execute`, and
the gateway's span recorder read failures through here, so each kind of database error is
classified once. Nothing here reads an error's message for logging: a message, and a Postgres
`DETAIL`, may quote a refused value.
"""

import psycopg
import psycopg.errors
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

# Failures a later try may not meet: the connection or the network, a pool or statement timeout,
# and what Postgres reports as an operational error (a deadlock, a serialization failure, a
# cancelled statement). Anything else (a value or constraint the database refuses, a bug) would
# fail every retry the same way.
_TRANSIENT_FAILURES = (OperationalError, InterfaceError, PoolTimeoutError, TimeoutError, OSError)


def is_nul_error(orig: object) -> bool:
    """Whether Postgres or psycopg refused a NUL character.

    psycopg refuses a NUL in a text parameter itself, before anything is sent, so that error
    carries no SQLSTATE and only its message names the cause. A `\\u0000` escape inside a jsonb
    value is refused by the server as `untranslatable_character` (22P05), whose detail names
    `\\u0000`; the same SQLSTATE also covers encoding conversions, which are not the client's fault.
    """
    if isinstance(orig, psycopg.errors.UntranslatableCharacter):
        return "\\u0000" in (orig.diag.message_detail or "")
    return isinstance(orig, psycopg.DataError) and "NUL" in str(orig)


def is_statement_timeout(orig: object) -> bool:
    """Whether a statement outlived `statement_timeout` (Postgres cancels it: `QueryCanceled`)."""
    return isinstance(orig, psycopg.errors.QueryCanceled)


def is_busy(failure: BaseException) -> bool:
    """Whether the database was too busy to answer: no pooled connection came free in time, or
    Postgres cancelled a statement at `statement_timeout`. Answered with a 503 and `Retry-After`.
    """
    if isinstance(failure, PoolTimeoutError):
        return True
    return isinstance(failure, OperationalError) and is_statement_timeout(failure.orig)


def is_transient(failure: BaseException) -> bool:
    """Whether a later try of the failed database work may succeed."""
    if isinstance(failure, DBAPIError) and failure.connection_invalidated:
        return True
    return isinstance(failure, _TRANSIENT_FAILURES)


def sqlstate(failure: BaseException) -> str | None:
    """The SQLSTATE of a database error (`22P05`, say), or None for any other failure.

    Safe to log, unlike the error's message.
    """
    code = getattr(getattr(failure, "orig", None), "sqlstate", None)
    return code if isinstance(code, str) else None
