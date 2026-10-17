"""What the `create_export` job shares between its trace and attribution writers."""

import contextlib
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.rls import bind_project

# Longest a single read may run. The lease is kept alive meanwhile, so this only ends a query
# that has plainly run away.
STATEMENT_TIMEOUT_MS = 120_000


ERROR_TOO_LARGE = "EXPORT_TOO_LARGE"


class ExportFailedError(Exception):
    """The export cannot finish and a retry would not help: the row is failed with `code`."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class TooLargeError(ExportFailedError):
    """More rows than `MAX_EXPORT_TRACES` turned up while streaming (new ones arrived)."""

    def __init__(self) -> None:
        super().__init__(ERROR_TOO_LARGE)


@dataclass
class Tally:
    """What the stream wrote, filled in by the generator the upload consumes."""

    rows: int = 0
    size_bytes: int = 0
    # Set the moment the row was updated to point at the object; from then on the object is live.
    recorded: bool = False


@contextlib.asynccontextmanager
async def bound(
    factory: async_sessionmaker[AsyncSession],
    project_id: uuid.UUID,
    *,
    statement_timeout_ms: int | None = None,
) -> AsyncIterator[AsyncSession]:
    """A session whose transaction sees only `project_id`'s rows. Callers commit what they write.

    With `statement_timeout_ms`, a statement of this transaction that runs longer is cancelled.
    """
    async with factory() as db:
        await bind_project(db, project_id)
        if statement_timeout_ms is not None:
            await db.execute(
                text("SELECT set_config('statement_timeout', :ms, true)"),
                {"ms": str(statement_timeout_ms)},
            )
        yield db
