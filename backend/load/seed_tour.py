"""Seed the product tour (`tour.js`) on top of a project that `seed.py` filled.

Reads the project id from the credentials file `seed.py` wrote and adds to that project, through
each domain's own service (the calls `app.services.demo_evals` makes for the hosted demo):

* users who sign in with a password: owners of the project's organization, each with a random
  password, so k6 can read with a session cookie and measure the sign-in itself;
* a project API key with `scores:write`, for `POST /v1/scores`;
* three datasets of 200 items, each item copied from a seeded trace (`add_from_traces`);
* two prompts with three versions each;
* one heuristic evaluator (no provider call);
* one annotation queue of 100 traces, on a boolean score config;
* an experiment on the first dataset with two finished runs, so the comparison has data;
* one trace of 5 000 spans, stored through `app.ingest.pipeline.ingest_spans` as `seed.py` does.

The experiment's runs are inserted finished (status `done`, one item per dataset item, totals
computed by `experiment_totals`), not executed: executing them needs the worker and a provider,
and the gateway credential `seed_gateway.py` makes belongs to another organization. The
experiment still needs a priced credential to pass its checks, so it gets an `openai` credential
with a made-up key, sealed with the load keyring (`LOAD_CREDENTIALS_KEYS`, as `seed_gateway.py`);
nothing ever calls it.

The passwords, the scores key and the ids k6 reads are written to a JSON file only the current
user can read, and never printed.

    cd backend
    export LOAD_DATABASE_URL=postgresql+psycopg://postgres:...@127.0.0.1:55433/spanlight
    uv run python load/seed_tour.py --credentials-file ../.local/load/credentials.json \
        --tour-file ../.local/load/tour-credentials.json

Connect as the database owner, as `seed.py` does.
"""

import argparse
import asyncio
import json
import os
import random
import secrets
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.annotations import service as annotations
from app.annotations.schemas import ItemsIn, QueueSpec
from app.config import Settings
from app.core.scopes import KeyScope
from app.core.security import hash_password
from app.datasets import service as datasets
from app.datasets.schemas import DatasetSpec
from app.db.models import (
    ExperimentItem,
    ExperimentRun,
    ExperimentRunStatus,
    Membership,
    MembershipRole,
    Project,
    ProviderCredential,
    ProviderKind,
    Span,
    Trace,
    User,
)
from app.db.models.telemetry import SpanKind
from app.db.rls import bind_project
from app.db.session import create_engine, create_session_factory
from app.evals import experiment_queries
from app.evals import service as evaluators
from app.evals.config import EvaluatorSpec, HeuristicEvaluatorConfig
from app.evals.evaluators.heuristic import ContainsRule
from app.evals.experiment_config import ExperimentConfig, ExperimentSpec, PromptVersionTarget
from app.evals.experiment_totals import ItemFacts, summarize_items, totals_json
from app.evals.experiments import create_experiment
from app.evals.service import Actor
from app.gateway.credentials import seal_api_key
from app.ingest.pipeline import IngestTarget, ingest_spans
from app.ingest.schemas import MAX_SPANS_PER_BATCH
from app.prompts import service as prompts
from app.prompts.schemas import PromptCreate, VersionCreate
from app.scores import configs as score_configs
from app.scores.schemas import ScoreConfigSpec
from app.scores.types import ScoreDataType
from seed_data import _build_trace
from seed_gateway import DEFAULT_CREDENTIALS_KEYS
from seed_workspace import _add_key

DATASETS = 3
ITEMS_PER_DATASET = 200  # also the most trace ids one `add_from_traces` call takes
QUEUE_ITEMS = 100  # MAX_TRACES_PER_ADD is 200, so one call
SAMPLE = 50
PROMPT_NAMES = ("tour-summarize", "tour-classify")
PROMPT_VERSIONS = 3
EVALUATOR_NAME = "tour_mentions_order"
SCORE_CONFIG_NAME = "tour_review"
MODEL = "gpt-4o-mini"  # priced for `openai`, which the experiment's checks require
BIG_TRACE_SPANS = 5_000
# Sign-in throttle: 20 password hashes per email and address in 15 minutes
# (`MAX_HASHES_PER_EMAIL_IP`), so the login scenario spreads its sign-ins over several users.
DEFAULT_LOGIN_USERS = 6
# Each candidate trace is asked for its first LLM span; extra rows cover traces without one.
CANDIDATES = DATASETS * ITEMS_PER_DATASET * 2


class TourSeedError(Exception):
    """The tour cannot be seeded; the message is printed and the exit status is 1."""


@dataclass(frozen=True)
class Options:
    credentials_file: Path
    tour_file: Path
    login_users: int
    credentials_keys: str


@dataclass(frozen=True)
class Sample:
    trace_id: str
    span_id: str
    session_id: str | None


def say(message: str) -> None:
    print(f"seed-tour: {message}", flush=True)


def write_credentials(path: Path, content: dict[str, Any]) -> None:
    """Write the tour's credentials and ids where only the current user can read them."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)  # the mode below only applies to a file that is created
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(content, handle)


async def recent_samples(db: AsyncSession, project_id: uuid.UUID) -> list[Sample]:
    """The project's newest traces that have an LLM span, with the first such span."""
    first_llm = (
        select(Span.span_id)
        .where(
            Span.project_id == Trace.project_id,
            Span.trace_id == Trace.trace_id,
            Span.kind == SpanKind.LLM,
        )
        .order_by(Span.started_at, Span.span_id)
        .limit(1)
        .scalar_subquery()
    )
    rows = await db.execute(
        select(Trace.trace_id, Trace.session_id, first_llm.label("span_id"))
        .where(Trace.project_id == project_id)
        .order_by(Trace.started_at.desc(), Trace.trace_id)
        .limit(CANDIDATES)
    )
    return [Sample(row.trace_id, row.span_id, row.session_id) for row in rows if row.span_id]


async def add_users(
    db: AsyncSession, org_id: uuid.UUID, count: int
) -> tuple[list[User], list[dict[str, str]]]:
    users: list[User] = []
    logins: list[dict[str, str]] = []
    run = secrets.token_hex(4)
    for number in range(count):
        email = f"tour-{run}-{number}@example.com"  # .invalid and .test fail EmailStr
        password = secrets.token_urlsafe(18)
        user = User(email=email, password_hash=hash_password(password), name=f"Tour {number}")
        db.add(user)
        users.append(user)
        logins.append({"email": email, "password": password})
    await db.flush()
    db.add_all(Membership(org_id=org_id, user_id=u.id, role=MembershipRole.OWNER) for u in users)
    return users, logins


async def add_prompts(
    db: AsyncSession, org_id: uuid.UUID, project_id: uuid.UUID, actor_id: uuid.UUID, now: datetime
) -> dict[str, list[uuid.UUID]]:
    """Each prompt with its versions' ids, version 1 first."""
    versions: dict[str, list[uuid.UUID]] = {}
    for name in PROMPT_NAMES:

        def template(number: int, prompt: str = name) -> dict[str, str]:
            return {
                "kind": "text",
                "text": f"{prompt} v{number}: answer the customer. {{{{input}}}}",
            }

        spec = PromptCreate.model_validate(
            {"name": name, "kind": "text", "template": template(1), "commit_message": "v1"}
        )
        _, first = await prompts.create_prompt(db, org_id, project_id, actor_id, spec, now=now)
        versions[name] = [first.id]
        for number in range(2, PROMPT_VERSIONS + 1):
            body = VersionCreate.model_validate(
                {"template": template(number), "commit_message": f"v{number}"}
            )
            version = await prompts.create_version(
                db, org_id, project_id, actor_id, name, body, now=now
            )
            versions[name].append(version.id)
    return versions


async def add_finished_runs(
    db: AsyncSession,
    experiment_id: uuid.UUID,
    targets: list[PromptVersionTarget],
    items: list[tuple[uuid.UUID, Any, Any, dict[str, Any]]],
    actor_id: uuid.UUID,
    now: datetime,
) -> list[uuid.UUID]:
    """One `done` run per target, with an item per dataset item and its totals. The second run
    passes more items than the first, so the comparison shows a difference."""
    run_ids: list[uuid.UUID] = []
    for index, target in enumerate(targets):
        rng = random.Random(f"tour-run:{index}")  # noqa: S311 - reproducible test data
        run = ExperimentRun(
            experiment_id=experiment_id,
            number=await experiment_queries.next_run_number(db, experiment_id),
            target=target.model_dump(mode="json"),
            status=ExperimentRunStatus.DONE.value,
            started_at=now - timedelta(minutes=10 - index * 5),
            finished_at=now - timedelta(minutes=8 - index * 5),
            started_by=actor_id,
            trigger="manual",
        )
        db.add(run)
        await db.flush()
        facts: list[ItemFacts] = []
        for item_id, item_input, expected, metadata in items:
            scores = {EVALUATOR_NAME: rng.random() < 0.55 + 0.15 * index}
            latency = rng.uniform(300, 1500)
            cost = Decimal(rng.randint(80, 400)) / Decimal(1_000_000)
            db.add(
                ExperimentItem(
                    run_id=run.id,
                    dataset_item_id=item_id,
                    output={"text": f"Answer {index + 1} to the order.", "finish_reason": "stop"},
                    input=item_input,
                    expected_output=expected,
                    item_metadata=metadata,
                    scores=scores,
                    latency_ms=latency,
                    cost_usd=cost,
                )
            )
            facts.append(ItemFacts(latency, cost, None, scores, None))
        run.totals = totals_json(summarize_items(facts, boolean_scores=frozenset({EVALUATOR_NAME})))
        run_ids.append(run.id)
    return run_ids


async def seed_objects(
    db: AsyncSession, project: Project, samples: list[Sample], options: Options, now: datetime
) -> dict[str, Any]:
    org_id, project_id = project.org_id, project.id
    users, logins = await add_users(db, org_id, options.login_users)
    actor_id = users[0].id
    actor = Actor(org_id=org_id, user_id=actor_id, ip=None)
    scores_key = _add_key(db, project_id, actor_id, "tour-scores", KeyScope.SCORES_WRITE)

    dataset_ids: list[uuid.UUID] = []
    first_items: list[tuple[uuid.UUID, Any, Any, dict[str, Any]]] = []
    for number in range(DATASETS):
        chunk = samples[number * ITEMS_PER_DATASET : (number + 1) * ITEMS_PER_DATASET]
        dataset = await datasets.create_dataset(
            db, org_id, project_id, actor_id, DatasetSpec(name=f"tour-{number + 1}"), now=now
        )
        found = await datasets.add_from_traces(
            db, project_id, dataset.id, [s.trace_id for s in chunk], None, now=now
        )
        if not found.item_ids:
            raise TourSeedError(f"dataset tour-{number + 1} got no items from the seeded traces")
        dataset_ids.append(dataset.id)
        if number == 0:
            first_items = [
                (item_id, item.input, item.expected_output, item.metadata)
                for item_id, item in zip(found.item_ids, found.items, strict=True)
            ]
    say(f"{DATASETS} datasets added ({len(first_items)} items in the first)")

    versions = await add_prompts(db, org_id, project_id, actor_id, now)
    evaluator = await evaluators.create_evaluator(
        db,
        project_id,
        actor,
        EvaluatorSpec(
            name=EVALUATOR_NAME,
            config=HeuristicEvaluatorConfig(
                kind="heuristic", check=ContainsRule(rule="contains", needle="order")
            ),
        ),
        now=now,
    )

    config = await score_configs.create_config(
        db,
        org_id,
        project_id,
        actor_id,
        ScoreConfigSpec(name=SCORE_CONFIG_NAME, data_type=ScoreDataType.BOOLEAN),
        now=now,
    )
    # The score tour.js posts; configured here so its first requests do not race to create it.
    await score_configs.create_config(
        db,
        org_id,
        project_id,
        actor_id,
        ScoreConfigSpec(
            name="tour_quality", data_type=ScoreDataType.NUMERIC, min_value=0, max_value=1
        ),
        now=now,
    )
    queue = await annotations.create_queue(
        db,
        org_id,
        project_id,
        actor_id,
        QueueSpec(name="tour-review", score_config_ids=[config.id]),
        now=now,
    )
    await annotations.add_items(
        db, project_id, queue.id, ItemsIn(trace_ids=[s.trace_id for s in samples[:QUEUE_ITEMS]])
    )
    say("prompts, evaluator and annotation queue added")

    settings = Settings(credentials_keys=SecretStr(options.credentials_keys))
    sealed = seal_api_key(secrets.token_urlsafe(24), settings=settings)
    credential = ProviderCredential(
        org_id=org_id,
        provider=ProviderKind.OPENAI,
        name="tour (never called)",
        base_url=None,
        ciphertext=sealed.ciphertext,
        key_id=sealed.key_id,
    )
    db.add(credential)
    await db.flush()
    prompt_versions = versions[PROMPT_NAMES[0]]
    targets = [
        PromptVersionTarget(
            kind="prompt_version",
            prompt_version_id=version_id,
            credential_id=credential.id,
            model=MODEL,
        )
        for version_id in prompt_versions[:2]
    ]
    experiment = await create_experiment(
        db,
        project_id,
        actor,
        ExperimentSpec(
            name="tour-v1-vs-v2",
            dataset_id=dataset_ids[0],
            config=ExperimentConfig(target=targets[0], max_items=ITEMS_PER_DATASET),
            evaluator_ids=[evaluator.id],
        ),
        max_items_ceiling=ITEMS_PER_DATASET,
        now=now,
    )
    run_ids = await add_finished_runs(db, experiment.id, targets, first_items, actor_id, now)
    say("experiment with two finished runs added")
    return {
        "users": logins,
        "scores_key": scores_key,
        "dataset_ids": [str(i) for i in dataset_ids],
        "prompt_names": list(PROMPT_NAMES),
        "evaluator_id": str(evaluator.id),
        "queue_id": str(queue.id),
        "experiment_id": str(experiment.id),
        "run_ids": [str(i) for i in run_ids],
    }


async def store_big_trace(
    session_factory: async_sessionmaker[AsyncSession], project: Project, now: datetime
) -> str:
    """One trace of `BIG_TRACE_SPANS` spans (a root and its children), in API-sized batches."""
    rng = random.Random("tour-big-trace")  # noqa: S311 - reproducible test data
    # Its children run one after another for about an hour; starting 12 hours back keeps the
    # whole trace in the past.
    spans = _build_trace(rng, BIG_TRACE_SPANS, now - timedelta(hours=12))
    target = IngestTarget(project_id=project.id, capture_payloads=True, org_id=project.org_id)
    for start in range(0, len(spans), MAX_SPANS_PER_BATCH):
        batch = spans[start : start + MAX_SPANS_PER_BATCH]
        async with session_factory() as session:
            outcome = await ingest_spans(session, target, batch, now=now)
            if outcome.rejected or outcome.accepted != len(batch):
                first = outcome.rejected[0].reason if outcome.rejected else "none reported"
                raise TourSeedError(f"big trace: {outcome.accepted} of {len(batch)} ({first})")
            await session.commit()
    trace_id: str = spans[0]["trace_id"]
    return trace_id


def parse_options(argv: list[str]) -> Options:
    parser = argparse.ArgumentParser(description="Seed the product tour load test.")
    parser.add_argument(
        "--credentials-file", type=Path, required=True, help="the JSON file seed.py wrote"
    )
    parser.add_argument(
        "--tour-file", type=Path, required=True, help="where to write the tour's JSON"
    )
    parser.add_argument(
        "--login-users",
        type=int,
        default=DEFAULT_LOGIN_USERS,
        help=f"users who sign in with a password (default: {DEFAULT_LOGIN_USERS})",
    )
    args = parser.parse_args(argv)
    if args.login_users < 1:
        parser.error("--login-users must be at least 1")
    return Options(
        credentials_file=args.credentials_file,
        tour_file=args.tour_file,
        login_users=args.login_users,
        credentials_keys=os.environ.get("LOAD_CREDENTIALS_KEYS", "").strip()
        or DEFAULT_CREDENTIALS_KEYS,
    )


async def run(options: Options, database_url: str) -> None:
    project_id = uuid.UUID(json.loads(options.credentials_file.read_text())["project_id"])
    engine = create_engine(database_url, pool_size=2)
    session_factory = create_session_factory(engine)
    now = datetime.now(UTC)
    try:
        async with session_factory() as db:
            await bind_project(db, project_id)  # datasets, prompts and the rest are under RLS
            project = await db.get(Project, project_id)
            if project is None:
                raise TourSeedError(f"no project {project_id}; run seed.py first")
            samples = await recent_samples(db, project_id)
            if len(samples) < DATASETS * ITEMS_PER_DATASET:
                raise TourSeedError(
                    f"{len(samples)} seeded traces with an LLM span, need "
                    f"{DATASETS * ITEMS_PER_DATASET}; seed more spans"
                )
            content = await seed_objects(db, project, samples, options, now)
            await db.commit()
        content["big_trace_id"] = await store_big_trace(session_factory, project, now)
        say(f"trace of {BIG_TRACE_SPANS:,} spans stored")
        content["project_id"] = str(project_id)
        content["traces"] = [
            {"trace_id": s.trace_id, "span_id": s.span_id} for s in samples[:SAMPLE]
        ]
        sessions = dict.fromkeys(s.session_id for s in samples if s.session_id)
        content["session_ids"] = list(sessions)[:SAMPLE]
        write_credentials(options.tour_file, content)
        say(f"credentials and ids written to {options.tour_file}")
    finally:
        await engine.dispose()


def main() -> None:
    options = parse_options(sys.argv[1:])
    # Deliberately not DATABASE_URL, as in seed.py.
    database_url = os.environ.get("LOAD_DATABASE_URL", "")
    if not database_url:
        sys.exit("seed-tour: set LOAD_DATABASE_URL to the owner URL of the database to fill")
    try:
        asyncio.run(run(options, database_url))
    except TourSeedError as error:
        sys.exit(f"seed-tour: {error}")


if __name__ == "__main__":
    main()
