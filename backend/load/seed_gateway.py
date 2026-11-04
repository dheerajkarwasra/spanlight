"""Seed a Spanlight database for the gateway overhead load test.

Creates a workspace (the same organization, owner and project `seed_workspace.py` builds for the
other scripts), then the three pieces a call through the gateway needs: an `openai_compatible`
credential on the fake provider (`fake_provider.py`, run by `compose.load.yaml`), a route that
sends every call to it once, and a gateway key without limits or cache. The fake provider's API
key is sealed with the keyring the stack runs with, so the gateway opens it the way it opens any
credential.

The gateway key is written to a JSON file only the current user can read and is never printed.

`--budget-spans N` also prepares the budget admission measurement: a second gateway key, N priced
LLM spans of that key this UTC month (one trace per 10 spans, inserted in one statement, not
through ingestion), and one enabled monthly `block` budget on that key at $1,000,000, which
never refuses but makes every call through the key read the month's history (ADR 0017). It also
raises the project's retention to 31 days, which a monthly budget needs (the default is 30). The
second key is written to the same file as `budget_key`.

    cd backend
    export LOAD_DATABASE_URL=postgresql+psycopg://postgres:...@127.0.0.1:55433/spanlight
    uv run python load/seed_gateway.py --credentials-file ../.local/load/gateway-credentials.json

Connect as the database owner (the `postgres` user of the Compose stack), as `seed.py` does: this
is a test harness, not a product path, so it does not go through row-level security.

The keyring must be the one the api and worker run with. `compose.load.yaml` gives them
LOAD_CREDENTIALS_KEYS, or the test keyring below when that is unset, and this script reads the
same variable with the same default. The keyring seals nothing but a made-up key for the fake
provider.
"""

import argparse
import asyncio
import json
import os
import secrets
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pydantic import SecretStr
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.budgets.schemas import BudgetSpec
from app.budgets.service import create_budget
from app.budgets.types import BudgetAction, BudgetPeriod, BudgetScope
from app.config import Settings
from app.core.security import GATEWAY_KEY_PREFIX, generate_key
from app.db.models import (
    GatewayKey,
    GatewayRoute,
    GatewayRouteVersion,
    Project,
    ProviderCredential,
    ProviderKind,
)
from app.db.rls import bypass_rls
from app.db.session import create_engine, create_session_factory
from app.gateway.credentials import base_url_for, seal_api_key
from app.gateway.route_config import FallbackPolicy, RetryPolicy, RouteConfig, Target
from seed_workspace import Workspace, WorkspaceError, create_workspace

# The keyring of the load stack: key id `load`, then 32 bytes of ASCII text in base64. Public on
# purpose, like the other throwaway values of a load run; keep it equal to the default of
# LOAD_CREDENTIALS_KEYS in compose.load.yaml.
DEFAULT_CREDENTIALS_KEYS = "load:c3BhbmxpZ2h0LWxvYWQtdGVzdC1vbmx5LWtleS0wMDE="
# Where the api container reaches the fake provider. The adapter appends `/chat/completions`.
DEFAULT_PROVIDER_BASE_URL = "http://fake-provider:9000/v1"
# The model the fake provider's canned answer names; the k6 script requests it.
MODEL = "gpt-4o-mini"
ENVIRONMENT = "load-test"
# A monthly block budget needs 31 days of retained history (`reserve`), the default keeps 30.
BUDGET_RETENTION_DAYS = 31
BUDGET_AMOUNT_USD = 1_000_000

# The budget key's history: span i of N starts at month start + (now - month start) * i / N and
# lasts 800 ms; spans 10t..10t+9 form trace t. 1000 input and 200 output tokens of gpt-4o-mini.
BUDGET_SPANS_SQL = """
WITH bounds AS (
    SELECT date_trunc('month', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC' AS start,
           now() - date_trunc('month', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC' AS span
), new_traces AS (
    INSERT INTO traces (project_id, trace_id, name, environment, started_at, ended_at,
                        span_count, error_count, input_tokens, output_tokens, cost_usd)
    SELECT :project_id, md5('budget-trace-' || t), 'budget-load', :environment,
           b.start + b.span * (t * 10.0 / :n), b.start + b.span * ((t * 10 + 9.0) / :n)
           + interval '800 milliseconds', least(10, :n - t * 10), 0,
           1000 * least(10, :n - t * 10), 200 * least(10, :n - t * 10),
           0.00027 * least(10, :n - t * 10)
    FROM bounds b, generate_series(0, (:n - 1) / 10) AS t
)
INSERT INTO spans (project_id, trace_id, span_id, kind, name, status, started_at, ended_at,
                   provider, model, input_tokens, output_tokens, cost_usd, source_key_id)
SELECT :project_id, md5('budget-trace-' || i / 10), left(md5('budget-span-' || i), 16), 'llm',
       'chat ' || :model, 'ok', b.start + b.span * (i::float8 / :n),
       b.start + b.span * (i::float8 / :n) + interval '800 milliseconds',
       'openai', :model, 1000, 200, 0.00027, :key_id
FROM bounds b, generate_series(0, :n - 1) AS i
"""


@dataclass(frozen=True)
class Options:
    credentials_file: Path
    provider_base_url: str
    credentials_keys: str
    budget_spans: int


def say(message: str) -> None:
    print(f"seed-gateway: {message}", flush=True)


def write_credentials(path: Path, gateway_keys: list[str], budget_key: str | None = None) -> None:
    """Write the keys where only the current user can read them."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)  # the mode below only applies to a file that is created
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    content: dict[str, object] = {
        "gateway_key": gateway_keys[0],
        "gateway_keys": gateway_keys,
        "model": MODEL,
    }
    if budget_key is not None:
        content.update(budget_key=budget_key, budget=True)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(content, handle)


async def add_gateway(
    session_factory: async_sessionmaker[AsyncSession],
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    options: Options,
) -> list[str]:
    """Add the credential, the route and two keys on it; returns their plaintexts."""
    settings = Settings(credentials_keys=SecretStr(options.credentials_keys))
    base_url = base_url_for(
        ProviderKind.OPENAI_COMPATIBLE, options.provider_base_url, allow_insecure=True
    )
    sealed = seal_api_key(secrets.token_urlsafe(24), settings=settings)
    now = datetime.now(UTC)
    async with session_factory() as session:
        credential = ProviderCredential(
            org_id=org_id,
            provider=ProviderKind.OPENAI_COMPATIBLE,
            name="fake-provider",
            base_url=base_url,
            ciphertext=sealed.ciphertext,
            key_id=sealed.key_id,
        )
        session.add(credential)
        await session.flush()
        # One attempt and no fallback: the fake provider never fails, and a retry would hide a
        # slow call instead of showing it.
        config = RouteConfig(
            targets=[Target(credential_id=credential.id)],
            retry=RetryPolicy(max_attempts=1),
            fallback=FallbackPolicy(on=[]),
        ).model_dump(mode="json")
        route = GatewayRoute(
            project_id=project_id,
            name="load-test",
            is_default=True,
            config=config,
            version=1,
            created_at=now,
            updated_at=now,
        )
        session.add(route)
        await session.flush()
        session.add(
            GatewayRouteVersion(
                route_id=route.id,
                version=route.version,
                project_id=project_id,
                config=config,
                created_at=now,
            )
        )
        # Two keys: the concurrency scenario spreads its calls over both, as two clients would,
        # because one key may hold only GATEWAY_MAX_IN_FLIGHT_PER_KEY of the slots.
        keys = [generate_key(GATEWAY_KEY_PREFIX) for _ in range(2)]
        # No rpm or tpm limit, no model list and no cache TTL: nothing may throttle the run, and
        # a cached answer would measure the cache instead of the gateway.
        session.add_all(
            GatewayKey(
                project_id=project_id,
                route_id=route.id,
                name=f"load-test-{index}",
                prefix=generated.prefix,
                secret_hash=generated.secret_hash,
                environment=ENVIRONMENT,
                allowed_models=[],
                default_tags=[],
                created_at=now,
            )
            for index, generated in enumerate(keys)
        )
        await session.commit()
    return [generated.plaintext for generated in keys]


async def add_budget(
    engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
    workspace: Workspace,
    spans: int,
) -> str:
    """Add the budget key, its spans and its budget; returns the key's plaintext."""
    now = datetime.now(UTC)
    async with session_factory() as session:
        await bypass_rls(session)
        await session.execute(
            update(Project)
            .where(Project.id == workspace.project_id)
            .values(retention_days=BUDGET_RETENTION_DAYS)
        )
        route_id = await session.scalar(
            select(GatewayRoute.id).where(GatewayRoute.project_id == workspace.project_id)
        )
        generated = generate_key(GATEWAY_KEY_PREFIX)
        key = GatewayKey(
            project_id=workspace.project_id,
            route_id=route_id,
            name="load-test-budget",
            prefix=generated.prefix,
            secret_hash=generated.secret_hash,
            environment=ENVIRONMENT,
            allowed_models=[],
            default_tags=[],
            created_at=now,
        )
        session.add(key)
        await session.flush()
        await session.execute(
            text(BUDGET_SPANS_SQL),
            {
                "project_id": workspace.project_id,
                "environment": ENVIRONMENT,
                "model": MODEL,
                "key_id": key.id,
                "n": spans,
            },
        )
        spec = BudgetSpec(
            name="load-test",
            scope=BudgetScope.GATEWAY_KEY,
            scope_id=str(key.id),
            period=BudgetPeriod.MONTHLY,
            amount_usd=BUDGET_AMOUNT_USD,
            action=BudgetAction.BLOCK,
        )
        await create_budget(
            session, workspace.org_id, workspace.project_id, workspace.owner_id, spec, now=now
        )
        await session.commit()
    async with engine.connect() as connection:
        autocommit = await connection.execution_options(isolation_level="AUTOCOMMIT")
        await autocommit.execute(text("VACUUM ANALYZE spans"))
    return generated.plaintext


def parse_options(argv: list[str]) -> Options:
    parser = argparse.ArgumentParser(description="Seed a database for the gateway load test.")
    parser.add_argument(
        "--credentials-file", type=Path, required=True, help="where to write the key (JSON)"
    )
    parser.add_argument(
        "--provider-base-url",
        default=DEFAULT_PROVIDER_BASE_URL,
        help=f"the fake provider as the api reaches it (default: {DEFAULT_PROVIDER_BASE_URL})",
    )
    parser.add_argument(
        "--budget-spans",
        type=int,
        default=0,
        help="add a budget key with this many spans this month and a block budget (default: 0)",
    )
    args = parser.parse_args(argv)
    if args.budget_spans < 0:
        parser.error("--budget-spans must be 0 or more")
    return Options(
        credentials_file=args.credentials_file,
        provider_base_url=args.provider_base_url,
        # A blank value is unset, as it is for the settings of the api.
        credentials_keys=os.environ.get("LOAD_CREDENTIALS_KEYS", "").strip()
        or DEFAULT_CREDENTIALS_KEYS,
        budget_spans=args.budget_spans,
    )


async def run(options: Options, database_url: str) -> None:
    engine = create_engine(database_url, pool_size=2)
    session_factory = create_session_factory(engine)
    try:
        workspace = await create_workspace(session_factory)
        say(f"project {workspace.project_id} created")
        gateway_keys = await add_gateway(
            session_factory, workspace.org_id, workspace.project_id, options
        )
        budget_key = None
        if options.budget_spans:
            budget_key = await add_budget(engine, session_factory, workspace, options.budget_spans)
            say(f"budget key, {options.budget_spans} spans and a block budget added")
        write_credentials(options.credentials_file, gateway_keys, budget_key)
        say(f"credential, route and keys added; keys written to {options.credentials_file}")
    finally:
        await engine.dispose()


def main() -> None:
    options = parse_options(sys.argv[1:])
    # Deliberately not DATABASE_URL, as in seed.py.
    database_url = os.environ.get("LOAD_DATABASE_URL", "")
    if not database_url:
        sys.exit("seed-gateway: set LOAD_DATABASE_URL to the owner URL of the database to fill")
    try:
        asyncio.run(run(options, database_url))
    except WorkspaceError as error:
        sys.exit(f"seed-gateway: {error}")


if __name__ == "__main__":
    main()
