"""Statement timeouts for the database sessions of API requests.

The cap is a connection option of the api's request engine (`app/main.py::_request_engine`,
`-c statement_timeout=...`), so it applies to every session on that engine, the gateway
recorder's included, and costs no round trip. It is not set on the role: the worker shares the
role and runs statements that are meant to take long. The worker and the idempotency and
rate-limit pools create their own engines without it.

A statement that outlives the cap is cancelled with ``QueryCanceled``, which the API maps to
``503 SERVICE_UNAVAILABLE``.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_SET_STATEMENT_TIMEOUT = text("SELECT set_config('statement_timeout', :milliseconds, true)")


async def set_statement_limit(session: AsyncSession, milliseconds: int) -> None:
    """Cancel the rest of the current transaction's statements after ``milliseconds`` each.

    ``0`` lifts the limit. Ends with the transaction.
    """
    await session.execute(_SET_STATEMENT_TIMEOUT, {"milliseconds": str(milliseconds)})


async def lift_statement_limit(session: AsyncSession) -> None:
    """Let the statements of the current transaction run as long as they need.

    For the few requests whose work is long by nature and cannot be split, such as deleting a
    project together with millions of spans through its foreign keys. Ends with the transaction:
    `set_config(..., is_local => true)` is the function form of ``SET LOCAL``, which overrides the
    connection default and cannot leak to the next user of a pooled connection.
    """
    await set_statement_limit(session, 0)
