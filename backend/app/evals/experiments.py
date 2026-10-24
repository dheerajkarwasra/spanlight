"""Experiments: create them, start runs and read a run's totals. Callers commit.

Every write runs in a transaction bound to the project (row-level security). A config is checked
before it is stored: the dataset, the evaluators and the prompt version must be the project's,
the credential the organization's, `max_items` within `EXPERIMENT_MAX_ITEMS`, and the model must
have a positive price, without which the cost cap could not be enforced
(`EXPERIMENT_MODEL_UNPRICED`).

Starting a run takes the experiment's row lock and works out the run's target: the
experiment's, or the same prompt at another version when the caller overrides it
(`TARGET_OVERRIDE_INVALID` otherwise; credential and model stay). It fails any unfinished run of
the experiment that can no longer finish (its worker died), then inserts a `queued` run holding
that target, with its `run_experiment` job, in the same transaction. One attempt only: the job
pays providers. A second unfinished run is refused by a partial unique index
(`RunInProgressError`). Audit metadata holds names and ids, never the config's messages.
"""

import uuid
from collections.abc import Iterable
from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.datasets import service as datasets
from app.db.errors import violated_constraint
from app.db.models import AuditAction, Experiment, ExperimentRun, ExperimentRunStatus
from app.evals import experiment_queries as queries
from app.evals.experiment_config import (
    AdHocTarget,
    ExperimentConfig,
    ExperimentSpec,
    PromptVersionTarget,
    TargetOverride,
)
from app.evals.experiment_errors import (
    ExperimentConfigError,
    ExperimentNameTakenError,
    ExperimentNotFoundError,
    ExperimentReferenceNotFoundError,
    RunInProgressError,
)
from app.evals.experiment_request import priced
from app.evals.experiment_totals import (
    Totals,
    needs_evaluator_errors,
    summarize_items,
    totals_from_json,
)
from app.evals.service import Actor
from app.gateway import service as gateway
from app.jobs.queue import enqueue
from app.pricing.cost import load_price_book
from app.prompts import service as prompts
from app.scores.queries import boolean_score_names
from app.services.audit import record_audit

NAME_CONSTRAINT = "experiments_project_name_key"
ACTIVE_RUN_CONSTRAINT = "experiment_runs_active_key"
RUN_JOB_ATTEMPTS = 1


async def create_experiment(
    db: AsyncSession,
    project_id: uuid.UUID,
    actor: Actor,
    spec: ExperimentSpec,
    *,
    max_items_ceiling: int,
    now: datetime,
) -> Experiment:
    """Check and store a new experiment. Raises `ExperimentNameTakenError`,
    `ExperimentConfigError` and `ExperimentReferenceNotFoundError`."""
    await _check(db, project_id, actor.org_id, spec, max_items_ceiling, now)
    experiment = Experiment(
        project_id=project_id,
        name=spec.name,
        dataset_id=spec.dataset_id,
        config=spec.config.model_dump(mode="json"),
        evaluator_ids=list(dict.fromkeys(spec.evaluator_ids)),
        created_by=actor.user_id,
        created_at=now,
    )
    db.add(experiment)
    try:
        async with db.begin_nested():
            await db.flush()
    except IntegrityError as error:
        if violated_constraint(error) == NAME_CONSTRAINT:
            raise ExperimentNameTakenError from None
        raise
    await record_audit(
        db,
        org_id=actor.org_id,
        actor_user_id=actor.user_id,
        action=AuditAction.EXPERIMENT_CREATE,
        target_type="experiment",
        target_id=experiment.id,
        ip=actor.ip,
        metadata={"name": experiment.name, "dataset_id": str(experiment.dataset_id)},
    )
    return experiment


async def _check(
    db: AsyncSession,
    project_id: uuid.UUID,
    org_id: uuid.UUID,
    spec: ExperimentSpec,
    max_items_ceiling: int,
    now: datetime,
) -> None:
    config = spec.config
    if config.max_items > max_items_ceiling:
        raise ExperimentConfigError(
            "VALIDATION_ERROR",
            f"An experiment run reads at most {max_items_ceiling} items.",
            "config.max_items",
        )
    if await datasets.project_dataset(db, project_id, spec.dataset_id) is None:
        raise ExperimentReferenceNotFoundError
    wanted = set(spec.evaluator_ids)
    found = await queries.project_evaluators(db, project_id, list(wanted))
    if len(found) != len(wanted):
        raise ExperimentReferenceNotFoundError
    target = config.target
    if isinstance(target, PromptVersionTarget):
        version = await prompts.project_version(db, project_id, target.prompt_version_id, hold=True)
        if version is None:
            raise ExperimentReferenceNotFoundError
    credential = await gateway.org_credential(db, org_id, target.credential_id)
    if credential is None:
        raise ExperimentReferenceNotFoundError
    prices = await load_price_book(db, org_id)
    # A zero price (an organization's override, say) would make the cost cap meaningless.
    if not priced(target, credential.provider, prices, now):
        raise ExperimentConfigError(
            "EXPERIMENT_MODEL_UNPRICED",
            f"The model {target.model} has no price, so the run's cost cap cannot be enforced.",
            "config.target.model",
        )


async def start_run(
    db: AsyncSession,
    project_id: uuid.UUID,
    actor: Actor,
    experiment_id: uuid.UUID,
    override: TargetOverride | None = None,
    *,
    now: datetime,
) -> ExperimentRun:
    """Queue a run of the experiment and its job. Raises `ExperimentNotFoundError`,
    `RunInProgressError` and `ExperimentConfigError` (`TARGET_OVERRIDE_INVALID`)."""
    experiment = await queries.get_experiment(db, project_id, experiment_id, lock=True)
    if experiment is None:
        raise ExperimentNotFoundError
    target = await _effective_target(db, project_id, experiment, override)
    await queries.reap_abandoned_runs(db, now, experiment_id=experiment.id)
    run = ExperimentRun(
        experiment_id=experiment.id,
        number=await queries.next_run_number(db, experiment.id),
        target=target.model_dump(mode="json"),
        status=ExperimentRunStatus.QUEUED.value,
        started_at=now,
        started_by=actor.user_id,
    )
    db.add(run)
    try:
        async with db.begin_nested():
            await db.flush()
    except IntegrityError as error:
        if violated_constraint(error) == ACTIVE_RUN_CONSTRAINT:
            raise RunInProgressError from None
        raise
    await _queue_job_and_audit(db, project_id, actor, experiment, run)
    return run


async def _effective_target(
    db: AsyncSession,
    project_id: uuid.UUID,
    experiment: Experiment,
    override: TargetOverride | None,
) -> PromptVersionTarget | AdHocTarget:
    """The experiment's target, or, with an override, the same prompt at the version it names
    (same credential and model)."""
    target = ExperimentConfig.model_validate(experiment.config).target
    if override is None:
        return target
    if isinstance(target, PromptVersionTarget):
        current = await prompts.project_version(db, project_id, target.prompt_version_id)
        wanted = await prompts.project_version(
            db, project_id, override.prompt_version_id, hold=True
        )
        if current is not None and wanted is not None and wanted.prompt_id == current.prompt_id:
            return target.model_copy(update={"prompt_version_id": wanted.id})
    raise ExperimentConfigError(
        "TARGET_OVERRIDE_INVALID",
        "A run can only switch a prompt-version experiment to another version of the same prompt.",
        "target_override.prompt_version_id",
    )


async def _queue_job_and_audit(
    db: AsyncSession,
    project_id: uuid.UUID,
    actor: Actor,
    experiment: Experiment,
    run: ExperimentRun,
) -> None:
    await enqueue(
        db,
        queries.RUN_JOB,
        {"run_id": str(run.id), "project_id": str(project_id)},
        dedupe_key=queries.run_job_key(run.id),
        max_attempts=RUN_JOB_ATTEMPTS,
    )
    await record_audit(
        db,
        org_id=actor.org_id,
        actor_user_id=actor.user_id,
        action=AuditAction.EXPERIMENT_RUN,
        target_type="experiment",
        target_id=experiment.id,
        ip=actor.ip,
        metadata={
            "name": experiment.name,
            "run_id": str(run.id),
            "prompt_version_id": run.target.get("prompt_version_id"),
        },
    )


async def get_run(
    db: AsyncSession, project_id: uuid.UUID, experiment_id: uuid.UUID, run_id: uuid.UUID
) -> tuple[Experiment, ExperimentRun]:
    """The experiment and its run. Raises `ExperimentNotFoundError`."""
    experiment = await queries.get_experiment(db, project_id, experiment_id)
    if experiment is None:
        raise ExperimentNotFoundError
    run = await queries.get_run(db, experiment.id, run_id)
    if run is None:
        raise ExperimentNotFoundError
    return experiment, run


async def run_totals(db: AsyncSession, project_id: uuid.UUID, run: ExperimentRun) -> Totals:
    """The totals stored when the run ended; computed from its items while it runs (or when a
    run was reaped before it could store them)."""
    if run.totals is not None:
        legacy = await legacy_evaluator_errors(db, [run])
        return totals_from_json(run.totals, legacy.get(run.id))
    return summarize_items(
        await queries.item_facts(db, run.id),
        boolean_scores=await boolean_score_names(db, project_id),
    )


async def legacy_evaluator_errors(
    db: AsyncSession, runs: Iterable[ExperimentRun | None]
) -> dict[uuid.UUID, int]:
    """For runs whose stored totals predate `evaluator_errors`, the count from their items (0
    included); other runs are absent. One query, none when every run's totals have the count."""
    old = [run.id for run in runs if run is not None and needs_evaluator_errors(run.totals)]
    counts = await queries.evaluator_error_counts(db, old)
    return {run_id: counts.get(run_id, 0) for run_id in old}
