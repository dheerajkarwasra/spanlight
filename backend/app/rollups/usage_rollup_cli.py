"""`spanlight recompute-usage-rollups`: the option parsing and the run behind the command."""

import asyncio
import re
from datetime import UTC, datetime, timedelta
from typing import Annotated

import typer

from app.config import get_settings
from app.db.session import create_engine, create_session_factory
from app.rollups.usage_rollup_backfill import backfill_usage_rollups

SinceOption = Annotated[
    str, typer.Option(help="How far back to recompute, as days (`30d`) or hours (`12h`).")
]
BatchSizeOption = Annotated[int, typer.Option(min=1, help="Traces per transaction.")]

_SINCE = re.compile(r"^(\d+)([dh])$")
_UNITS = {"d": timedelta(days=1), "h": timedelta(hours=1)}


def parse_since(value: str) -> timedelta:
    """`Nd` or `Nh` as a duration; anything else is a usage error (exit status 2)."""
    match = _SINCE.match(value)
    if match is None:
        raise typer.BadParameter(
            f"{value!r} is not a duration like 30d or 12h", param_hint="--since"
        )
    return int(match.group(1)) * _UNITS[match.group(2)]


def recompute_usage_rollups(since: str, batch_size: int) -> int:
    """Recompute the traces started within `since` of now; returns how many."""
    start = datetime.now(UTC) - parse_since(since)

    async def run() -> int:
        engine = create_engine(get_settings().database_url, pool_size=1)
        try:
            factory = create_session_factory(engine)
            return await backfill_usage_rollups(factory, since=start, batch_size=batch_size)
        finally:
            await engine.dispose()

    return asyncio.run(run())
