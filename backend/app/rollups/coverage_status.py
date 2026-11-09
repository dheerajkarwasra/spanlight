"""Whether `spanlight activate-aggregate-coverage` has run on this database."""

import structlog
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, async_sessionmaker

from app.rollups.activation import NEW_PROJECT_BOUNDARY

logger = structlog.get_logger(__name__)

ACTIVATE_COMMAND = "spanlight activate-aggregate-coverage --writers-drained"

# Activation sets the column default to the epoch literal; Postgres stores it as
# `'1970-01-01 00:00:00+00'::timestamp with time zone`. The literal is cast and compared as a
# timestamp, so its text formatting does not matter. A NULL default (migration 1027), any other
# expression or a missing column reads as inactive.
_COVERAGE_ACTIVE = text(
    """
    SELECT coalesce((
        SELECT substring(column_default FROM '^''([^'']*)''::timestamp with time zone$')
               ::timestamptz = CAST(:boundary AS timestamptz)
        FROM information_schema.columns
        WHERE table_schema = current_schema() AND table_name = 'projects'
          AND column_name = 'aggregate_history_unverified_before'
    ), false)
    """
)


async def coverage_active(conn: AsyncConnection) -> bool:
    return bool(await conn.scalar(_COVERAGE_ACTIVE, {"boundary": NEW_PROJECT_BOUNDARY}))


async def warn_if_coverage_inactive(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """Log once at start-up when coverage is off: rollup reads stay unknown until it is on.

    Never blocks start-up: an unreachable database is reported by readiness instead.
    """
    try:
        async with session_factory() as session:
            active = await coverage_active(await session.connection())
    except (SQLAlchemyError, OSError) as exc:
        logger.warning("aggregate_coverage_check_failed", error_type=type(exc).__name__)
        return
    if not active:
        logger.warning("aggregate_coverage_inactive", fix=ACTIVATE_COMMAND)
