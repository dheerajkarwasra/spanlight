"""A project's LLM tokens by price class: uncached input, output, cache read and cache write.

Read from `cost_rollups_daily`, so the window widens to every UTC day it touches, and the mix
covers all environments: that table has no environment column. Reasoning tokens are output
tokens, priced at the output rate, so they are no class of their own.
"""

import uuid
from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import TokenMixOut
from app.api.window import TimeWindow
from app.end_users.days import midnight
from app.rollups.coverage_reads import CoverageState, read_covered
from app.rollups.dirty import COST_DAILY

# sum(bigint) is numeric in Postgres; cast back so the counts come out as int.
_MIX = text("""
    SELECT sum(uncached_input_tokens)::bigint AS uncached_input_tokens,
           sum(output_tokens)::bigint AS output_tokens,
           sum(cache_read_tokens)::bigint AS cache_read_tokens,
           sum(cache_write_5m_tokens + cache_write_1h_tokens)::bigint AS cache_write_tokens,
           sum(fee_usd) AS fee_usd, sum(cost_usd) AS cost_usd
    FROM cost_rollups_daily
    WHERE project_id = :project_id AND day >= :first_day AND day <= :last_day
""")


async def token_mix(db: AsyncSession, project_id: uuid.UUID, window: TimeWindow) -> TokenMixOut:
    """Absent figures stay unknown; coverage and stored values share one SQL snapshot."""
    first_day = window.start.date()
    last_day = (window.end - timedelta(microseconds=1)).date()
    coverage = CoverageState()
    [row] = await read_covered(
        db,
        _MIX,
        {"project_id": project_id, "first_day": first_day, "last_day": last_day},
        project_id,
        midnight(first_day),
        midnight(last_day + timedelta(days=1)),
        coverage,
        relevant_consumers=COST_DAILY,
    )
    return TokenMixOut(
        first_day=first_day,
        last_day=last_day,
        uncached_input_tokens=row.uncached_input_tokens,
        output_tokens=row.output_tokens,
        cache_read_tokens=row.cache_read_tokens,
        cache_write_tokens=row.cache_write_tokens,
        fee_usd=row.fee_usd,
        cost_usd=row.cost_usd,
        **coverage.metadata(),
    )
