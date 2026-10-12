"""The demo evaluation seed, end to end: the `seed_demo_evals` job on a demo project with
recorded answers and the demo Anthropic credential, as on the hosted demo."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.models import (
    DatasetItem,
    Evaluator,
    Experiment,
    ExperimentRun,
    ExperimentRunStatus,
    Prompt,
    ProviderCredential,
    ProviderKind,
)
from app.db.rls import bind_project, bypass_rls
from app.ingest.pipeline import IngestTarget, ingest_spans
from app.jobs.context import TaskContext
from app.jobs.queue import ClaimedJob, claim_next, enqueue
from app.jobs.tasks.seed_demo_evals import SEED_DEMO_EVALS_JOB, run_seed_demo_evals
from app.services.demo import DemoWorkspace, ensure_demo_workspace
from app.services.demo_evals import NeedMoreTraces
from app.services.demo_evals_data import ANSWER_INPUT_PREFIX, ITEMS
from tests.helpers import new_trace_id, span

SessionFactory = async_sessionmaker[AsyncSession]


async def _workspace_with_credential(session_factory: SessionFactory) -> DemoWorkspace:
    async with session_factory() as db:
        workspace = await ensure_demo_workspace(db)
        await bypass_rls(db)
        db.add(
            ProviderCredential(
                org_id=workspace.org.id,
                provider=ProviderKind.ANTHROPIC,
                name="demo-anthropic",
                ciphertext=b"sealed",
                key_id="test",
            )
        )
        await db.commit()
    return workspace


async def _record_answers(
    session_factory: SessionFactory, workspace: DemoWorkspace, count: int
) -> None:
    now = datetime.now(UTC)
    spans = [
        span(
            trace_id=new_trace_id(),
            start=now - timedelta(minutes=index + 1),
            provider="anthropic",
            model="claude-haiku-5-5",
            input={
                "messages": [
                    {
                        "role": "user",
                        "content": f"{ANSWER_INPUT_PREFIX} Refunds take 5 days.\n\n"
                        f"Customer: when is refund {index} back?",
                    }
                ]
            },
            output={"role": "assistant", "content": f"Within 5 days (refund {index})."},
        )
        for index in range(count)
    ]
    async with session_factory() as db:
        target = IngestTarget(
            project_id=workspace.project.id, capture_payloads=True, org_id=workspace.org.id
        )
        outcome = await ingest_spans(db, target, spans)
        await db.commit()
    assert outcome.accepted == count


async def _run_job(session_factory: SessionFactory, settings: Settings) -> None:
    async with session_factory() as db:
        await enqueue(db, SEED_DEMO_EVALS_JOB, max_attempts=1)
        await db.commit()
        job = await claim_next(db)
    assert isinstance(job, ClaimedJob)
    context = TaskContext(job=job, settings=settings, session_factory=session_factory)
    await run_seed_demo_evals(context, {})


async def _counts(session_factory: SessionFactory, workspace: DemoWorkspace) -> dict[str, int]:
    async with session_factory() as db:
        await bind_project(db, workspace.project.id)
        return {
            name: int(await db.scalar(select(func.count()).select_from(model)) or 0)
            for name, model in (
                ("prompts", Prompt),
                ("items", DatasetItem),
                ("evaluators", Evaluator),
                ("experiments", Experiment),
                ("runs", ExperimentRun),
            )
        }


async def test_seed_job_refuses_with_too_few_answers(
    session_factory: SessionFactory, settings: Settings
) -> None:
    workspace = await _workspace_with_credential(session_factory)
    await _record_answers(session_factory, workspace, ITEMS - 1)
    with pytest.raises(NeedMoreTraces):
        await _run_job(session_factory, settings)
    counts = await _counts(session_factory, workspace)
    assert counts["prompts"] == 0
    assert counts["experiments"] == 0


async def test_seed_job_creates_everything_then_queues_the_second_run(
    session_factory: SessionFactory, settings: Settings
) -> None:
    workspace = await _workspace_with_credential(session_factory)
    await _record_answers(session_factory, workspace, ITEMS)

    await _run_job(session_factory, settings)
    counts = await _counts(session_factory, workspace)
    # `support/answer` and the judge's `judges/relevance`.
    assert counts["prompts"] == 2
    assert counts["items"] == ITEMS
    assert counts["evaluators"] == 1
    assert counts["experiments"] == 1
    assert counts["runs"] == 1

    # While the first run is queued, a repeat queues nothing.
    await _run_job(session_factory, settings)
    assert (await _counts(session_factory, workspace))["runs"] == 1

    async with session_factory() as db:
        await bind_project(db, workspace.project.id)
        await db.execute(
            update(ExperimentRun).values(
                status=ExperimentRunStatus.DONE, finished_at=datetime.now(UTC)
            )
        )
        await db.commit()
    await _run_job(session_factory, settings)
    assert (await _counts(session_factory, workspace))["runs"] == 2
