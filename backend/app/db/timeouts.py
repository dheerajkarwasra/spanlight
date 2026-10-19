"""Statement timeouts for the database sessions of API requests.

The timeout is set with ``set_config(..., is_local => true)`` (the function form of
``SET LOCAL``), so it ends with the transaction and cannot leak to the next user of a pooled
connection. It is not set on the role or the connection: the worker shares the role and runs
statements that are meant to take long, and the streaming exports and the idempotency and
rate-limit pools have their own sessions.
"""

from sqlalchemy import Connection, event, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, SessionTransaction

_SET_STATEMENT_TIMEOUT = text("SELECT set_config('statement_timeout', :milliseconds, true)")


def limit_statement_time(session: AsyncSession, milliseconds: int) -> None:
    """Cancel any statement of this session that runs longer than ``milliseconds``.

    Postgres then answers ``QueryCanceled``, which the API maps to ``503 SERVICE_UNAVAILABLE``.
    A transaction begins again after each commit, so the limit is applied at the start of every
    one of them and covers the whole request, not only the part before the first commit.
    """
    parameters = {"milliseconds": str(milliseconds)}

    @event.listens_for(session.sync_session, "after_begin")
    def _apply(_: Session, __: SessionTransaction, connection: Connection) -> None:
        connection.execute(_SET_STATEMENT_TIMEOUT, parameters)


async def set_statement_limit(session: AsyncSession, milliseconds: int) -> None:
    """Cancel the rest of the current transaction's statements after ``milliseconds`` each.

    ``0`` lifts the limit. Ends with the transaction.
    """
    await session.execute(_SET_STATEMENT_TIMEOUT, {"milliseconds": str(milliseconds)})


async def lift_statement_limit(session: AsyncSession) -> None:
    """Let the statements of the current transaction run as long as they need.

    For the few requests whose work is long by nature and cannot be split, such as deleting a
    project together with millions of spans through its foreign keys. Ends with the transaction.
    """
    await set_statement_limit(session, 0)
