"""A project's LLM tokens by price class: uncached input, output, cache read and cache write.

Read from `cost_rollups_daily`, so the window widens to every UTC day it touches, and the mix
covers all environments: that table has no environment column. Reasoning tokens are output
tokens, priced at the output rate, so they are no class of their own.
"""

import uuid
from datetime import timedelta

from sqlalchemy import BigInteger, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import TokenMixOut
from app.api.window import TimeWindow
from app.db.models import CostRollupDaily as R


async def token_mix(db: AsyncSession, project_id: uuid.UUID, window: TimeWindow) -> TokenMixOut:
    """Sums over the days from `window.start` to the instant before `window.end`; every figure
    is None when no rollup row falls in them. Runs under the caller's row-level security."""
    first_day = window.start.date()
    last_day = (window.end - timedelta(microseconds=1)).date()
    # sum(bigint) is numeric in Postgres; cast back so the counts come out as int.
    row = (
        await db.execute(
            select(
                cast(func.sum(R.uncached_input_tokens), BigInteger),
                cast(func.sum(R.output_tokens), BigInteger),
                cast(func.sum(R.cache_read_tokens), BigInteger),
                cast(func.sum(R.cache_write_5m_tokens + R.cache_write_1h_tokens), BigInteger),
                func.sum(R.fee_usd),
                func.sum(R.cost_usd),
            ).where(R.project_id == project_id, R.day >= first_day, R.day <= last_day)
        )
    ).one()
    uncached, output, cache_read, cache_write, fee, cost = row
    return TokenMixOut(
        first_day=first_day,
        last_day=last_day,
        uncached_input_tokens=uncached,
        output_tokens=output,
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
        fee_usd=fee,
        cost_usd=cost,
    )
