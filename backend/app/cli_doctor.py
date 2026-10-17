"""`spanlight doctor ...`: administrative commands for the Doctor's detectors."""

import asyncio
import uuid
from typing import Annotated

import typer

from app.config import get_settings
from app.db.session import create_engine, create_session_factory
from app.insights.precision import PrecisionRow
from app.insights.precision_cli_queries import UnknownOrganizationError, precision_rows_all

doctor_app = typer.Typer(help="The Doctor's detectors.", no_args_is_help=True)

COLUMNS = ("kind", "right", "wrong", "n", "precision", "wilson_low", "wilson_high", "meets_target")
UNKNOWN = "—"


async def _rows(org_id: uuid.UUID | None) -> list[PrecisionRow]:
    engine = create_engine(get_settings().database_url, pool_size=1)
    try:
        async with create_session_factory(engine)() as db:
            return await precision_rows_all(db, org_id=org_id)
    finally:
        await engine.dispose()


def _cells(row: PrecisionRow) -> tuple[str, ...]:
    ratios = (row.precision, row.wilson_low, row.wilson_high)
    return (
        row.kind,
        str(row.right),
        str(row.wrong),
        str(row.n),
        *(UNKNOWN if ratio is None else str(ratio) for ratio in ratios),
        "yes" if row.meets_target else "no",
    )


def precision_table(rows: list[PrecisionRow]) -> str:
    """A Markdown table, one line per detector kind."""
    lines = [
        "| " + " | ".join(COLUMNS) + " |",
        "|" + "|".join("---" for _ in COLUMNS) + "|",
        *("| " + " | ".join(_cells(row)) + " |" for row in rows),
    ]
    return "\n".join(lines)


@doctor_app.command("precision")
def precision_command(
    org: Annotated[
        uuid.UUID | None, typer.Option(help="Only this organization's projects (its id).")
    ] = None,
) -> None:
    """Print the Doctor's precision per detector kind from the members' "Was this right?" votes.

    Sums the votes of every project, or of one organization's with --org, reading across
    projects the way the worker does. Prints counts only: no project, insight or user. A kind
    with no votes shows "—" for its ratios. `meets_target` is precision >= 0.8 with a Wilson 95 %
    lower bound >= 0.6 on at least 20 votes.
    """
    try:
        rows = asyncio.run(_rows(org))
    except UnknownOrganizationError:
        raise typer.BadParameter(f"no organization with id {org}", param_hint="--org") from None
    typer.echo(precision_table(rows))
