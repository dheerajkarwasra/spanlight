"""`db_errors.is_busy`: which database failures mean "too busy right now" (answered 503)."""

import psycopg.errors
import pytest
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from app.core.db_errors import is_busy

STATEMENT = "SELECT 1"


def _wrapped(wrapper: type[OperationalError | IntegrityError], sqlstate: str) -> Exception:
    """`wrapper` around the psycopg error of `sqlstate`, as SQLAlchemy raises it."""
    return wrapper(STATEMENT, None, psycopg.errors.lookup(sqlstate)("refused"))


@pytest.mark.parametrize(
    ("failure", "busy"),
    [
        (PoolTimeoutError("QueuePool limit of size 5 overflow 10 reached"), True),
        (_wrapped(OperationalError, "57014"), True),  # query_canceled: a statement timeout
        (_wrapped(OperationalError, "40P01"), False),  # a deadlock: not a busy database
        (_wrapped(OperationalError, "08006"), True),  # a lost connection: transient, a 503
        (_wrapped(IntegrityError, "23505"), False),
        (TimeoutError("an asyncio timeout"), False),
        (RuntimeError("a bug"), False),
    ],
    ids=[
        "pool_timeout",
        "statement_timeout",
        "deadlock",
        "connection_failure",
        "unique_violation",
        "builtin_timeout",
        "other",
    ],
)
def test_is_busy_cases(failure: Exception, busy: bool) -> None:
    assert is_busy(failure) is busy
