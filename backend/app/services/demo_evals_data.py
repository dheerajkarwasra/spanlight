"""What the demo evaluation seed is made of and where it reads from.

The prompt texts, the names of the seeded objects and the reads (the demo credential, the demo
answers to copy into a dataset, the objects a previous run created). No writes: those go through
each domain's service in `demo_evals`.
"""

import uuid
from collections.abc import Sequence
from typing import Final

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.datasets.schemas import SpanSource, TraceWithSpans
from app.db.models import (
    Dataset,
    Evaluator,
    Experiment,
    ProviderCredential,
    ProviderKind,
    Span,
    Trace,
)
from app.db.models.telemetry import SpanKind
from app.evals.evaluators.heuristic import text_of
from app.jobs.tasks.demo_flow import ANSWER_SYSTEM

PROMPT_NAME: Final = "support/answer"
DATASET_NAME: Final = "support-questions"
EVALUATOR_NAME: Final = "relevance"
JUDGE_TEMPLATE: Final = "relevance"
# The cheapest priced Claude; it judges and it answers.
MODEL: Final = "claude-haiku-5-5"
ITEMS: Final = 50
# One experiment compares the two prompt versions: it targets version 1, and its second run
# overrides the target with version 2.
EXPERIMENT_NAME: Final = "answer-v1-vs-v2"
PROMPT_VERSIONS: Final = (1, 2)
# The spend caps of the hosted demo: one judge month, and each experiment run.
JUDGE_MONTHLY_BUDGET_USD: Final = "0.50"
JUDGE_SAMPLING_RATE: Final = "0.05"
RUN_MAX_COST_USD: Final = "0.50"

# The user message of the demo flow's answer call starts like this (see `demo_flow`).
ANSWER_INPUT_PREFIX: Final = "FAQ entry:"

# Version 1 is the demo flow's answer prompt as it runs today. A dataset item's input is the
# whole user message of one recorded answer call (FAQ entry and customer question), so the
# user message is the `input` variable.
V1_SYSTEM: Final = ANSWER_SYSTEM
V2_SYSTEM: Final = (
    "You are a friendly, concise support agent. Answer the customer in at most two sentences, "
    "using only the FAQ entry provided and putting the answer first. Do not add facts the "
    "entry does not contain. If the entry does not cover the question, say in one sentence "
    "that a human will follow up."
)
USER_MESSAGE: Final = "{{input}}"
V1_COMMIT: Final = "The answer prompt of the demo support bot."
V2_COMMIT: Final = "Tighter: two sentences, answer first, no added facts."


async def anthropic_credential(db: AsyncSession, org_id: uuid.UUID) -> ProviderCredential | None:
    """The organization's oldest Anthropic credential (the demo gateway's, once set up)."""
    return await db.scalar(
        select(ProviderCredential)
        .where(
            ProviderCredential.org_id == org_id,
            ProviderCredential.provider == ProviderKind.ANTHROPIC,
        )
        .order_by(ProviderCredential.created_at, ProviderCredential.id)
        .limit(1)
    )


async def recent_answers(
    db: AsyncSession, project_id: uuid.UUID, limit: int
) -> Sequence[TraceWithSpans]:
    """The project's newest recorded demo answer calls with a stored payload, one per trace.

    The input of each is `{"input": <the user message>}` (the variables of the seeded prompt) and
    the recorded answer is kept as the span's output. `db` is bound to the project.
    """
    message = Span.input["messages"][0]["content"].as_string()
    rows = await db.execute(
        select(
            Span.trace_id,
            Span.span_id,
            Span.started_at,
            Span.model,
            message.label("message"),
            Span.output,
            Trace.environment,
        )
        .join(Trace, and_(Trace.project_id == Span.project_id, Trace.trace_id == Span.trace_id))
        .where(
            Span.project_id == project_id,
            Span.kind == SpanKind.LLM,
            message.like(f"{ANSWER_INPUT_PREFIX}%"),
            Span.output.is_not(None),
        )
        .order_by(Span.started_at.desc(), Span.span_id)
        .limit(limit)
    )
    return [
        TraceWithSpans(
            trace_id=row.trace_id,
            environment=row.environment,
            spans=[
                SpanSource(
                    span_id=row.span_id,
                    kind=SpanKind.LLM.value,
                    started_at=row.started_at,
                    model=row.model,
                    input={"input": row.message},
                    output=text_of(row.output, list_role="assistant"),
                )
            ],
        )
        for row in rows
    ]


async def named_dataset(db: AsyncSession, project_id: uuid.UUID, name: str) -> Dataset | None:
    return await db.scalar(
        select(Dataset).where(Dataset.project_id == project_id, Dataset.name == name)
    )


async def named_evaluator(db: AsyncSession, project_id: uuid.UUID, name: str) -> Evaluator | None:
    return await db.scalar(
        select(Evaluator).where(Evaluator.project_id == project_id, Evaluator.name == name)
    )


async def named_experiment(db: AsyncSession, project_id: uuid.UUID, name: str) -> Experiment | None:
    return await db.scalar(
        select(Experiment).where(Experiment.project_id == project_id, Experiment.name == name)
    )
