"""Writing the bundled price snapshots into the database: model prices and server-tool fees.

Every snapshot, current and historical, is inserted beside the others on its natural key, and an
existing row is left untouched, so a span stored under an older snapshot keeps its cost and
version whenever this runs (startup, the worker, `spanlight sync-prices`).
"""

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import new_id
from app.db.models import ModelPrice
from app.db.models import ServerToolFee as ServerToolFeeRow
from app.pricing.prices import ALL_SNAPSHOTS


async def sync_seed_prices(session: AsyncSession) -> int:
    """Insert every bundled price snapshot, the current and the historical ones: its model prices
    and its server-tool fees. Existing rows are left untouched.

    Each snapshot has its own `effective_from`, so its rows land beside the others and price the
    spans that start after it; a span stored under an older snapshot keeps its cost and version,
    and a fresh install prices an older span as an upgraded one does.
    Returns the number of rows inserted, prices and fees together. The caller commits.
    """
    return await _insert_prices(session) + await _insert_fees(session)


async def _insert_prices(session: AsyncSession) -> int:
    statement = (
        insert(ModelPrice)
        .values(
            [
                {
                    "id": new_id(),
                    "provider": seed.provider,
                    "model_pattern": seed.model_pattern,
                    "input_per_mtok": seed.input_per_mtok,
                    "output_per_mtok": seed.output_per_mtok,
                    "cached_input_per_mtok": seed.cached_input_per_mtok,
                    "cache_write_5m_per_mtok": seed.cache_write_5m_per_mtok,
                    "cache_write_1h_per_mtok": seed.cache_write_1h_per_mtok,
                    "min_prompt_tokens": seed.min_prompt_tokens,
                    "effective_from": snapshot.effective_from,
                    "version": snapshot.version,
                }
                for snapshot in ALL_SNAPSHOTS
                for seed in snapshot.prices
            ]
        )
        .on_conflict_do_nothing(
            index_elements=["provider", "model_pattern", "effective_from", "min_prompt_tokens"]
        )
        .returning(ModelPrice.id)
    )
    result = await session.execute(statement)
    return len(result.all())


async def _insert_fees(session: AsyncSession) -> int:
    rows = [
        {
            "id": new_id(),
            "provider": fee.provider,
            "tool": fee.tool,
            "usd_per_1k_requests": fee.usd_per_1k_requests,
            "effective_from": snapshot.effective_from,
            "version": snapshot.version,
        }
        for snapshot in ALL_SNAPSHOTS
        for fee in snapshot.fees
    ]
    if not rows:
        return 0
    statement = (
        insert(ServerToolFeeRow)
        .values(rows)
        .on_conflict_do_nothing(index_elements=["provider", "tool", "effective_from"])
        .returning(ServerToolFeeRow.id)
    )
    result = await session.execute(statement)
    return len(result.all())
