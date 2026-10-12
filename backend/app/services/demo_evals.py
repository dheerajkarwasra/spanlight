"""Seed the demo project with a prompt, a dataset, a judge and an experiment.

`seed_evals` is what `spanlight demo seed-evals` runs. It builds the objects the evaluation
screens need to show something real: the prompt `support/answer` in two versions, the dataset
`support-questions` copied from recorded demo answers, the judge `relevance` and the experiment
`answer-v1-vs-v2`, which gets a run of each prompt version. Every object goes through its domain's
service, so audit rows, limits and checks are the ones a user would meet. The actor is the demo
user.

Idempotent: an object that exists by name is kept as it is. An experiment runs one run at a time,
so the first call queues the version 1 run and a later call, once that run has ended, queues the
version 2 run (the target overridden). With both runs there, repeating the command spends
nothing. The caller commits.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.datasets import queries as dataset_queries
from app.datasets import service as datasets
from app.datasets.importers import items_from_traces
from app.datasets.schemas import DatasetSpec
from app.db.models import Dataset, ExperimentRun, ExperimentRunStatus, ProviderCredential
from app.db.rls import bind_project
from app.evals import experiment_queries
from app.evals import service as evaluators
from app.evals.config import EvaluatorSpec, JudgeConfig
from app.evals.experiment_config import (
    ExperimentConfig,
    ExperimentSpec,
    PromptVersionTarget,
    TargetOverride,
)
from app.evals.experiments import create_experiment, start_run
from app.evals.judge_templates import materialize_template
from app.evals.service import Actor
from app.prompts import queries as prompt_queries
from app.prompts import service as prompts
from app.prompts.schemas import PromptCreate, VersionCreate
from app.services import demo_evals_data as data
from app.services.demo import DemoWorkspace

logger = structlog.get_logger(__name__)


class NoAnthropicCredentialError(Exception):
    """The demo organization has no Anthropic credential to judge and answer with."""


class NeedMoreTraces(Exception):  # noqa: N818 - the name the command is specified with
    """The demo project has fewer recorded answers than the dataset needs."""

    def __init__(self, found: int) -> None:
        super().__init__(f"need {data.ITEMS} traces, found {found}")
        self.found = found


@dataclass(frozen=True)
class SeedReport:
    """What a seed run created. Empty `created` and `runs` mean everything was already there."""

    created: dict[str, uuid.UUID] = field(default_factory=dict)
    runs: dict[str, uuid.UUID] = field(default_factory=dict)
    items: int = 0
    # The version 2 run is not queued yet: an experiment runs one run at a time.
    waiting: bool = False

    @property
    def already_seeded(self) -> bool:
        return not (self.created or self.runs or self.waiting)


async def seed_evals(db: AsyncSession, workspace: DemoWorkspace, now: datetime) -> SeedReport:
    """Create what is missing, and queue the next run the experiment lacks. Raises
    `NoAnthropicCredentialError` and `NeedMoreTraces`; on either, nothing has been written that
    the caller should keep (it rolls back)."""
    credential = await data.anthropic_credential(db, workspace.org.id)
    if credential is None:
        raise NoAnthropicCredentialError
    await bind_project(db, workspace.project.id)  # prompts, datasets and the rest are under RLS
    actor = Actor(org_id=workspace.org.id, user_id=workspace.user.id, ip=None)
    created: dict[str, uuid.UUID] = {}
    versions = await _seed_prompt(db, workspace, created, now)
    logger.info("demo_seed_step_done", step="prompt")
    dataset = await _seed_dataset(db, workspace, created, now)
    logger.info("demo_seed_step_done", step="dataset")
    evaluator_id = await _seed_judge(db, workspace, actor, credential, created, now)
    logger.info("demo_seed_step_done", step="judge")
    experiment_id = await _seed_experiment(
        db, workspace, actor, versions, (dataset, credential, evaluator_id), created, now
    )
    logger.info("demo_seed_step_done", step="experiment")
    runs, waiting = await _seed_runs(db, workspace, actor, experiment_id, versions, now)
    logger.info("demo_seed_step_done", step="runs")
    items = await dataset_queries.count_items(db, dataset.id)
    return SeedReport(created=created, runs=runs, items=items, waiting=waiting)


async def _seed_prompt(
    db: AsyncSession, workspace: DemoWorkspace, created: dict[str, uuid.UUID], now: datetime
) -> dict[int, uuid.UUID]:
    """The prompt `support/answer` with both versions and its labels, created together when the
    prompt is missing. Returns the version ids by number."""
    org_id, project_id, actor_id = workspace.org.id, workspace.project.id, workspace.user.id
    existing = await prompt_queries.get_prompt(db, project_id, data.PROMPT_NAME)
    if existing is None:
        first_spec = PromptCreate.model_validate(
            {
                "name": data.PROMPT_NAME,
                "kind": "chat",
                "description": "Answers a customer from one FAQ entry.",
                "template": _chat(data.V1_SYSTEM),
                "commit_message": data.V1_COMMIT,
            }
        )
        prompt, first = await prompts.create_prompt(
            db, org_id, project_id, actor_id, first_spec, now=now
        )
        second_spec = VersionCreate.model_validate(
            {"template": _chat(data.V2_SYSTEM), "commit_message": data.V2_COMMIT}
        )
        second = await prompts.create_version(
            db, org_id, project_id, actor_id, data.PROMPT_NAME, second_spec, now=now
        )
        for label, labelled in (("production", second), ("staging", first)):
            await prompts.set_label(
                db, org_id, project_id, actor_id, data.PROMPT_NAME, label, labelled.version, now=now
            )
        created["prompt"] = prompt.id
        return {1: first.id, 2: second.id}
    found: dict[int, uuid.UUID] = {}
    for number in data.PROMPT_VERSIONS:
        version = await prompt_queries.get_version(db, existing.id, number)
        if version is not None:
            found[number] = version.id
    return found


def _chat(system: str) -> dict[str, object]:
    return {
        "kind": "chat",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": data.USER_MESSAGE},
        ],
    }


async def _seed_dataset(
    db: AsyncSession, workspace: DemoWorkspace, created: dict[str, uuid.UUID], now: datetime
) -> Dataset:
    """The dataset `support-questions`, filled from the newest recorded answers. Raises
    `NeedMoreTraces` before anything is written when there are too few."""
    project_id = workspace.project.id
    existing = await data.named_dataset(db, project_id, data.DATASET_NAME)
    if existing is not None:
        return existing
    found = items_from_traces(await data.recent_answers(db, project_id, data.ITEMS), None)
    if len(found.items) < data.ITEMS:
        raise NeedMoreTraces(len(found.items))
    spec = DatasetSpec(
        name=data.DATASET_NAME, description="Recent demo support questions with their FAQ entry."
    )
    dataset = await datasets.create_dataset(
        db, workspace.org.id, project_id, workspace.user.id, spec, now=now
    )
    await datasets.add_items(db, project_id, dataset.id, found.items, now=now)
    created["dataset"] = dataset.id
    return dataset


async def _seed_judge(
    db: AsyncSession,
    workspace: DemoWorkspace,
    actor: Actor,
    credential: ProviderCredential,
    created: dict[str, uuid.UUID],
    now: datetime,
) -> uuid.UUID:
    """The judge `relevance`, sampling 5 % of demo traffic under a $0.50 monthly budget."""
    project_id = workspace.project.id
    existing = await data.named_evaluator(db, project_id, data.EVALUATOR_NAME)
    if existing is not None:
        return existing.id
    version = await materialize_template(
        db, actor.org_id, project_id, actor.user_id, data.JUDGE_TEMPLATE, now=now
    )
    spec = EvaluatorSpec(
        name=data.EVALUATOR_NAME,
        config=JudgeConfig(
            kind="llm_judge",
            prompt_version_id=version.id,
            credential_id=credential.id,
            model=data.MODEL,
        ),
        sampling_rate=Decimal(data.JUDGE_SAMPLING_RATE),
        monthly_budget_usd=Decimal(data.JUDGE_MONTHLY_BUDGET_USD),
    )
    evaluator = await evaluators.create_evaluator(db, project_id, actor, spec, now=now)
    created["evaluator"] = evaluator.id
    return evaluator.id


async def _seed_experiment(
    db: AsyncSession,
    workspace: DemoWorkspace,
    actor: Actor,
    versions: dict[int, uuid.UUID],
    parts: tuple[Dataset, ProviderCredential, uuid.UUID],
    created: dict[str, uuid.UUID],
    now: datetime,
) -> uuid.UUID | None:
    """The experiment `answer-v1-vs-v2` on prompt version 1, or None when the prompt no longer
    has its version 1."""
    project_id = workspace.project.id
    existing = await data.named_experiment(db, project_id, data.EXPERIMENT_NAME)
    if existing is not None:
        return existing.id
    if 1 not in versions:
        return None
    dataset, credential, evaluator_id = parts
    target = PromptVersionTarget(
        kind="prompt_version",
        prompt_version_id=versions[1],
        credential_id=credential.id,
        model=data.MODEL,
    )
    spec = ExperimentSpec(
        name=data.EXPERIMENT_NAME,
        dataset_id=dataset.id,
        config=ExperimentConfig(
            target=target, max_cost_usd=Decimal(data.RUN_MAX_COST_USD), max_items=data.ITEMS
        ),
        evaluator_ids=[evaluator_id],
    )
    experiment = await create_experiment(
        db, project_id, actor, spec, max_items_ceiling=data.ITEMS, now=now
    )
    created["experiment"] = experiment.id
    return experiment.id


async def _seed_runs(
    db: AsyncSession,
    workspace: DemoWorkspace,
    actor: Actor,
    experiment_id: uuid.UUID | None,
    versions: dict[int, uuid.UUID],
    now: datetime,
) -> tuple[dict[str, uuid.UUID], bool]:
    """Queue the run the experiment lacks: version 1 (its own target) first, then version 2 (the
    target overridden) once the first has ended. Returns the queued runs by name and whether
    version 2 still waits for a run in progress (or was just not due yet)."""
    if experiment_id is None:
        return {}, False
    project_id = workspace.project.id
    existing = await experiment_queries.list_runs(db, experiment_id, after=None, limit=2)
    if not existing:
        run = await start_run(db, project_id, actor, experiment_id, now=now)
        return {"v1": run.id}, 2 in versions
    if len(existing) > 1 or 2 not in versions:
        return {}, False
    if _active(existing[0]):
        return {}, True
    override = TargetOverride(prompt_version_id=versions[2])
    run = await start_run(db, project_id, actor, experiment_id, override, now=now)
    return {"v2": run.id}, False


def _active(run: ExperimentRun) -> bool:
    return run.status in (ExperimentRunStatus.QUEUED.value, ExperimentRunStatus.RUNNING.value)
