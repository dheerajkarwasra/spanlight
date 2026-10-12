"""`spanlight demo ...`: administrative commands for the public demo workspace."""

import asyncio
from datetime import UTC, datetime

import typer
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.session import create_engine, create_session_factory
from app.services.demo import ensure_demo_workspace
from app.services.demo_evals import (
    NeedMoreTraces,
    NoAnthropicCredentialError,
    SeedReport,
    seed_evals,
)

demo_app = typer.Typer(help="The public demo workspace.", no_args_is_help=True)


async def _seed() -> SeedReport:
    engine = create_engine(get_settings().database_url, pool_size=1)
    try:
        async with create_session_factory(engine)() as db:
            return await _seed_in(db)
    finally:
        await engine.dispose()


async def _seed_in(db: AsyncSession) -> SeedReport:
    workspace = await ensure_demo_workspace(db)
    report = await seed_evals(db, workspace, datetime.now(UTC))
    await db.commit()
    return report


def _report_text(report: SeedReport) -> str:
    if report.already_seeded:
        return f"already_seeded: nothing changed ({report.items} dataset items)."
    lines = ["Seeded the demo evaluations."]
    lines += [f"  {name}: {object_id}" for name, object_id in report.created.items()]
    lines += [f"  run {name}: {run_id} (queued)" for name, run_id in report.runs.items()]
    lines.append(f"  dataset items: {report.items}")
    if report.waiting:
        lines.append("The v2 run waits for the v1 run to end; run this command again then.")
    return "\n".join(lines)


@demo_app.command("seed-evals")
def seed_evals_command() -> None:
    """Seed the demo project with a prompt, a dataset, a judge and an experiment.

    Creates the prompt `support/answer` (version 1 is the demo bot's answer prompt, version 2 a
    tightened rewrite; `production` points at 2 and `staging` at 1), the dataset
    `support-questions` from the 50 newest recorded demo answers, the judge `relevance`, and the
    experiment `answer-v1-vs-v2`. An experiment runs one run at a time, so the first call queues
    the version 1 run and a second call, once that run has ended, queues the version 2 run. The
    worker executes the runs, which are real provider calls under the demo credential: each run
    is capped at $0.50.

    Needs 50 recorded demo answers with stored payloads and an Anthropic credential on the demo
    organization (set ANTHROPIC_API_KEY and let the worker's demo job run first). Safe to repeat:
    what exists is kept and nothing is queued again. Exits 1 with `need 50 traces, found N` when
    there are too few.
    """
    try:
        report = asyncio.run(_seed())
    except NoAnthropicCredentialError:
        typer.echo("Error: demo org has no Anthropic credential", err=True)
        raise typer.Exit(code=1) from None
    except NeedMoreTraces as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(_report_text(report))
