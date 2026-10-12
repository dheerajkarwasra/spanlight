"""Start an experiment run: load what its items share and move it from `queued` to `running`.

One transaction bound to the project, holding the run's row lock. A run that is no longer
`queued` (the reaper failed it, or this job ran before) is left alone. A run that cannot start
fails at once, with no provider call, with one of these errors:

* `experiment_config_invalid`: the stored config or the run's target no longer parses;
* `credentials_not_configured`: `CREDENTIALS_KEYS` is unset, so no credential can be opened;
* `credential_missing`: the credential was deleted;
* `prompt_version_missing`: the prompt version is gone;
* `model_unpriced`: the model lost its price, so the cost cap could not be enforced.

Evaluators deleted since the experiment was created are skipped, and so are disabled ones (a
disabled evaluator spends nothing).
"""

import uuid
from dataclasses import dataclass

import structlog
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Experiment, ExperimentRun, ExperimentRunStatus, Project, PromptVersion
from app.db.rls import bind_project
from app.evals import experiment_queries as queries
from app.evals.experiment_config import ExperimentConfig, PromptVersionTarget, parse_target
from app.evals.experiment_item import RunPlan
from app.evals.experiment_request import priced
from app.evals.experiment_totals import summarize_items, totals_json
from app.evals.runner_build import RunnerDeps
from app.gateway import service as gateway
from app.pricing.cost import load_price_book
from app.prompts import service as prompts

logger = structlog.get_logger(__name__)

TRACE_NAME_PREFIX = "experiment:"


@dataclass(frozen=True)
class Started:
    plan: RunPlan
    dataset_id: uuid.UUID
    max_items: int


async def start(deps: RunnerDeps, run_id: uuid.UUID, project_id: uuid.UUID) -> Started | None:
    """The started run, or None when it did not start (skipped, or failed at once)."""
    now = deps.clock()
    async with deps.sessions() as db, db.begin():
        await bind_project(db, project_id)
        run = await db.scalar(
            select(ExperimentRun)
            .where(ExperimentRun.id == run_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if run is None or run.status != ExperimentRunStatus.QUEUED:
            logger.info("experiment_run_skipped", run_id=str(run_id), project_id=str(project_id))
            return None
        experiment = await db.scalar(
            select(Experiment).where(
                Experiment.id == run.experiment_id, Experiment.project_id == project_id
            )
        )
        project = await db.get(Project, project_id)
        if experiment is None or project is None:
            return None
        loaded = await _plan(db, deps, run, experiment, project)
        if isinstance(loaded, str):
            run.status = ExperimentRunStatus.FAILED.value
            run.error = loaded
            run.finished_at = now
            run.totals = totals_json(summarize_items([]))
            logger.warning("experiment_run_not_started", run_id=str(run_id), error=loaded)
            return None
        run.status = ExperimentRunStatus.RUNNING.value
        run.heartbeat_at = now
        config = ExperimentConfig.model_validate(experiment.config)
        max_items = min(config.max_items, deps.settings.experiment_max_items)
        return Started(loaded, experiment.dataset_id, max_items)


async def _plan(
    db: AsyncSession,
    deps: RunnerDeps,
    run: ExperimentRun,
    experiment: Experiment,
    project: Project,
) -> RunPlan | str:
    """The run's plan, or the error it fails with."""
    try:
        config = ExperimentConfig.model_validate(experiment.config)
        target = parse_target(run.target)  # the experiment's, or a version override's
    except ValidationError:
        return "experiment_config_invalid"
    if not deps.settings.is_crypto_configured:
        return "credentials_not_configured"
    credential = await gateway.org_credential(db, project.org_id, target.credential_id)
    if credential is None:
        return "credential_missing"
    version: PromptVersion | None = None
    if isinstance(target, PromptVersionTarget):
        version = await prompts.project_version(db, project.id, target.prompt_version_id)
        if version is None:
            return "prompt_version_missing"
    prices = await load_price_book(db, project.org_id)
    if not priced(target, credential.provider, prices, deps.clock()):
        return "model_unpriced"
    evaluators = await queries.project_evaluators(db, project.id, experiment.evaluator_ids)
    return RunPlan(
        run_id=run.id,
        experiment_id=experiment.id,
        trace_name=f"{TRACE_NAME_PREFIX}{experiment.name}",
        project=project,
        credential=credential,
        target=target,
        version=version,
        evaluators=tuple(e for e in evaluators if e.enabled),
        prices=prices,
        max_cost_usd=config.max_cost_usd,
    )
