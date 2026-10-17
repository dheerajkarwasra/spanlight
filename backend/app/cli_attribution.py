"""`spanlight attribution ...`: operator commands for the daily attribution rollups."""

import asyncio
import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Annotated

import typer
from sqlalchemy.ext.asyncio import AsyncSession

from app.attribution.job_queries import promoted_keys_of
from app.attribution.refresh import RefreshResult, refresh_attribution, refresh_group
from app.config import get_settings
from app.db.models import Project
from app.db.rls import bind_project
from app.db.session import create_engine, create_session_factory
from app.end_users.days import ONE_DAY

attribution_app = typer.Typer(help="Daily attribution rollups.", no_args_is_help=True)

# Days recomputed by one statement; a longer range runs as several, each a transaction.
CHUNK_DAYS = 31


def _parse_day(value: str, param_hint: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise typer.BadParameter(
            f"{value!r} is not an ISO 8601 date", param_hint=param_hint
        ) from exc


def _chunks(days: Sequence[date]) -> list[list[date]]:
    return [list(days[start : start + CHUNK_DAYS]) for start in range(0, len(days), CHUNK_DAYS)]


async def _backfill(
    project_id: uuid.UUID, first: date, last: date, key: str | None
) -> RefreshResult:
    """Recompute the project's rollups over `[first, last]`, bound to the project, in chunks.

    Raises `typer.BadParameter` for an unknown project, a key that is not promoted, or a range
    that starts before the project's retention (the retention job would delete those rows).
    """
    engine = create_engine(get_settings().database_url, pool_size=1)
    try:
        session_factory = create_session_factory(engine)
        async with session_factory() as db:
            project = await db.get(Project, project_id)
            if project is None:
                raise typer.BadParameter(f"no project with id {project_id}", param_hint="--project")
            promoted = await promoted_keys_of(db, project_id) or []
            cutoff = (datetime.now(UTC) - timedelta(days=project.retention_days)).date()
        if first < cutoff:
            raise typer.BadParameter(
                f"the project's retention is {project.retention_days} days: days before "
                f"{cutoff.isoformat()} would be deleted by the next retention pass",
                param_hint="--from",
            )
        if key is not None and key not in promoted:
            raise typer.BadParameter(
                f"{key!r} is not a promoted key of the project (promoted: {promoted or 'none'})",
                param_hint="--key",
            )
        days = [first + offset * ONE_DAY for offset in range((last - first).days + 1)]
        total = RefreshResult()
        for chunk in _chunks(days):
            async with session_factory() as db:
                await bind_project(db, project_id)
                total += await _refresh_chunk(db, project_id, promoted, chunk, key)
                await db.commit()
        return total
    finally:
        await engine.dispose()


async def _refresh_chunk(
    db: AsyncSession,
    project_id: uuid.UUID,
    promoted: Sequence[str],
    days: Sequence[date],
    key: str | None,
) -> RefreshResult:
    if key is None:
        return await refresh_attribution(db, project_id, promoted, days)
    return await refresh_group(db, project_id, "metadata", key, days)


@attribution_app.command("backfill")
def backfill_command(
    project: Annotated[uuid.UUID, typer.Option(help="Project id.")],
    from_: Annotated[str, typer.Option("--from", help="First UTC day (inclusive), YYYY-MM-DD.")],
    to: Annotated[str, typer.Option(help="Last UTC day (inclusive), YYYY-MM-DD.")],
    key: Annotated[
        str | None,
        typer.Option(help="Only this promoted metadata key; by default every kind and key."),
    ] = None,
) -> None:
    """Recompute one project's daily attribution rollups over [--from, --to], both included.

    Use it for spans that arrived more than a week after their trace started, or to build the
    days before a key was promoted. The range may not start before the project's retention.
    """
    first = _parse_day(from_, "--from")
    last = _parse_day(to, "--to")
    if first > last:
        raise typer.BadParameter("--from must not be later than --to", param_hint="--from")
    result = asyncio.run(_backfill(project, first, last, key))
    typer.echo(
        f"Wrote {result.written} rollup rows, removed {result.removed}, "
        f"folded {result.folded} values."
    )
