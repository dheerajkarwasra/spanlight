"""Real Postgres fixtures with invocation-owned disposable databases.

By default each worker owns a Postgres 17 Testcontainer. TEST_DATABASE_URL may
supply an admin connection to a disposable spanlight_test database; its schema
is never reset. Each invocation creates and drops only its own UUID child DB.
Application connections remain NOSUPERUSER NOBYPASSRLS to exercise real RLS.
"""

import os
import re
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import httpx
import psycopg
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from testcontainers.postgres import PostgresContainer

from app.config import Settings
from app.db.migrations import upgrade_to_head
from app.db.session import create_engine, create_session_factory
from app.main import create_app
from app.pricing.cost import sync_seed_prices
from app.rollups import dirty
from app.rollups.activation import activate_aggregate_coverage
from tests.database_lifecycle import owned_database
from tests.helpers import Browser

_RUN_ID = uuid4().hex
_ACTIVE_BASE_URL: str | None = None
APP_ROLE = "spanlight_app_test"
APP_PASSWORD = "spanlight_app_test"
TEST_ORIGIN = "http://testserver"

# The gateway fixtures (fake provider, gateway workspace and context) and the budget fixtures
# (pinned app clock, budget world) for every test directory.
pytest_plugins = ["tests.gateway_fixtures", "tests.budget_fixtures"]

# The tables emptied before every test, and with them every table that has a foreign key to one of
# them, as `TRUNCATE ... CASCADE` would reach.
_TABLES_TO_EMPTY = (
    "exports",
    "span_rollups_hourly",
    "trace_rollups_hourly",
    "spans",
    "traces",
    "audit_events",
    "invites",
    "api_keys",
    "projects",
    "memberships",
    "sessions",
    "login_attempts",
    "throttle_events",
    "organizations",
    "email_tokens",
    "oauth_identities",
    "recovery_codes",
    "personal_access_tokens",
    "users",
    "jobs",
    "notification_outbox",
    "idempotency_keys",
    "worker_heartbeats",
    "rate_limit_buckets",
    "provider_incidents",
)
# The status feed rows are seeded by migration 0702: reset their poll state instead of deleting.
_RESET_STATUS_SOURCES = (
    "UPDATE provider_status_sources SET etag = NULL, last_modified = NULL, "
    "last_polled_at = NULL, last_success_at = NULL, last_outcome = NULL, "
    "consecutive_failures = 0, last_error_type = NULL"
)
# Statistics must not outlive the rows they describe. The suite empties tables by DELETE, so a
# table analyzed or vacuumed while it held an earlier test's rows keeps their row count and
# column statistics, or "0 rows in N pages" if autovacuum reached it just after the cleanup. The
# planner then sizes the next test's rows as one and picks plans many times slower, which failed
# wall-clock and plan-shape tests in CI about a minute into a run: autovacuum's first visit.
# So nothing but a test writes statistics: the harness's server runs without autovacuum, and each
# cleanup returns every table to "never analyzed" (`reltuples = -1`, no column statistics).
_FORGET_STATISTICS = (
    "; UPDATE pg_class SET reltuples = -1 "
    "WHERE reltuples <> -1 AND relkind = 'r' AND relnamespace = 'public'::regnamespace"
    "; DELETE FROM pg_statistic WHERE starelid IN "
    "(SELECT oid FROM pg_class WHERE relnamespace = 'public'::regnamespace)"
)
_NO_AUTOVACUUM = "postgres -c autovacuum=off"
# Without autovacuum the dead rows stay, and every cleanup's DELETE would read them again. A
# table past 1 MB is truncated instead: rare, so the cost that made TRUNCATE too slow for every
# test does not return. CASCADE reaches only tables that reference it, all of them emptied anyway.
_TRUNCATE_GROWN = """
DO $$
DECLARE grown text;
BEGIN
    SELECT string_agg(name, ', ') INTO grown FROM unnest(ARRAY[{tables}]) AS name
    WHERE pg_relation_size(name::regclass) > 1048576;
    IF grown IS NOT NULL THEN EXECUTE 'TRUNCATE ' || grown || ' CASCADE'; END IF;
END $$;
"""
# `_TABLES_TO_EMPTY` and the tables that reference them, transitively, and the sequences they own.
_EMPTIED_RELATIONS = """
WITH RECURSIVE emptied(oid) AS (
    SELECT unnest(%s::regclass[])::oid
    UNION
    SELECT c.conrelid FROM pg_constraint c JOIN emptied ON c.confrelid = emptied.oid
    WHERE c.contype = 'f'
)
SELECT
    array(SELECT oid::regclass::text FROM emptied),
    array(
        SELECT d.objid::regclass::text
        FROM pg_depend d
        JOIN emptied ON d.refobjid = emptied.oid
        JOIN pg_class s ON s.oid = d.objid AND s.relkind = 'S'
        WHERE d.classid = 'pg_class'::regclass AND d.deptype IN ('a', 'i')
    )
"""


def _base_database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL") or _ACTIVE_BASE_URL
    if url is None:
        raise RuntimeError("Testcontainer fixture has not started")
    parts = urlsplit(url)
    if (
        parts.scheme not in {"postgresql", "postgresql+psycopg"}
        or not re.fullmatch(r"spanlight_test(?:_[a-zA-Z0-9_]+)?", parts.path.lstrip("/"))
        or parts.query
        or parts.fragment
    ):
        raise ValueError("TEST_DATABASE_URL must name a disposable spanlight_test database")
    return url


def admin_database_url() -> str:
    """Only this invocation and worker's owned database, never the supplied parent."""
    parts = urlsplit(_base_database_url())
    worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
    if not re.fullmatch(r"(?:gw[0-9]+|main)", worker):
        raise ValueError("Invalid pytest worker name")
    return urlunsplit(parts._replace(path=f"/spanlight_test_{_RUN_ID}_{worker}"))


@pytest.fixture(scope="session")
def database_server() -> Iterator[None]:
    global _ACTIVE_BASE_URL  # noqa: PLW0603 - fixture owns the module-level URL for legacy callers.
    if "TEST_DATABASE_URL" in os.environ:
        _base_database_url()  # Reject unsafe names before any database connection.
        yield
        return
    with PostgresContainer(
        "postgres:17",
        username="postgres",
        password="postgres",
        dbname="spanlight_test",
        driver="psycopg",
    ).with_command(_NO_AUTOVACUUM) as postgres:
        _ACTIVE_BASE_URL = postgres.get_connection_url()
        try:
            yield
        finally:
            _ACTIVE_BASE_URL = None


def _libpq(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://")


def _with_credentials(url: str, user: str, password: str) -> str:
    parts = urlsplit(url)
    host = parts.hostname or "localhost"
    netloc = f"{user}:{password}@{host}:{parts.port}" if parts.port else f"{user}:{password}@{host}"
    return urlunsplit(parts._replace(netloc=netloc))


@pytest.fixture(scope="session")
def app_database_url(database_server: None) -> Iterator[str]:
    admin_url = admin_database_url()
    libpq_url = _libpq(admin_url)
    with psycopg.connect(_libpq(_base_database_url()), autocommit=True) as connection:
        # The role and the databases are cluster-wide and xdist workers start together, so they
        # take turns here. Advisory locks are per database: every worker takes it in the base one.
        connection.execute("SELECT pg_advisory_lock(hashtext(%s))", (APP_ROLE,))
        exists = connection.execute(
            "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = %s", (APP_ROLE,)
        ).fetchone()
        if exists is None:
            connection.execute(
                f"CREATE ROLE {APP_ROLE} LOGIN PASSWORD '{APP_PASSWORD}' "
                "NOSUPERUSER NOBYPASSRLS NOCREATEROLE"
            )
        elif exists != (False, False):
            raise ValueError("Test application role must be NOSUPERUSER NOBYPASSRLS")
        worker_database = urlsplit(libpq_url).path.lstrip("/")
    with owned_database(_libpq(_base_database_url()), worker_database):
        with psycopg.connect(libpq_url, autocommit=True) as connection:
            connection.execute("DROP SCHEMA IF EXISTS public CASCADE")
            connection.execute(f"CREATE SCHEMA public AUTHORIZATION {APP_ROLE}")
            database = urlsplit(libpq_url).path.lstrip("/")
            connection.execute(f'GRANT CREATE, CONNECT ON DATABASE "{database}" TO {APP_ROLE}')

        url = _with_credentials(admin_url, APP_ROLE, APP_PASSWORD)
        upgrade_to_head(url)
        activate_aggregate_coverage(url, fresh_install=True)
        yield url


@pytest.fixture(scope="session")
def settings(app_database_url: str) -> Settings:
    return Settings(
        database_url=app_database_url,
        app_base_url=TEST_ORIGIN,
        allowed_origins=["http://localhost:5173"],
        secret_key="test-secret-key-at-least-32-characters",
        metrics_token="test-metrics-token",
        anthropic_api_key=None,
        demo_enabled=True,
        log_json=False,
        log_level="WARNING",
        _env_file=None,
    )


@pytest.fixture(scope="session")
async def engine(settings: Settings) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(settings.database_url, pool_size=5)
    async with create_session_factory(engine)() as session:
        await sync_seed_prices(session)
        await session.commit()
    yield engine
    await engine.dispose()


@pytest.fixture
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(engine)


@pytest.fixture(scope="session")
async def _empty_tables(
    app_database_url: str,
) -> AsyncIterator[Callable[[], Awaitable[None]]]:
    """Empty the test tables and restart their sequences, as `TRUNCATE ... RESTART IDENTITY`.

    A TRUNCATE of 69 mostly empty tables cost ~140 ms a test, half the suite's time, so this
    deletes their rows instead, in one round trip. It runs as the admin user, which sees every
    row past RLS, with foreign key checks off (`session_replication_role`), so no order matters.
    """
    async with await psycopg.AsyncConnection.connect(_libpq(admin_database_url())) as admin:
        cursor = await admin.execute(_EMPTIED_RELATIONS, (list(_TABLES_TO_EMPTY),))
        row = await cursor.fetchone()
        assert row is not None
        tables, sequences = row
        await admin.commit()
        statements = (
            "SET LOCAL session_replication_role = replica; "
            + _TRUNCATE_GROWN.format(tables=", ".join(f"'{table}'" for table in tables))
            + "".join(f"DELETE FROM {table}; " for table in tables)
            + "SELECT setval(seqrelid, seqstart, false) FROM pg_sequence "
            + f"WHERE seqrelid = ANY('{{{','.join(sequences)}}}'::regclass[]); "
            + _RESET_STATUS_SOURCES
            + _FORGET_STATISTICS
        )

        async def empty() -> None:
            async with admin.transaction():
                await admin.execute(statements)

        yield empty


@pytest.fixture(autouse=True)
async def clean_database(engine: AsyncEngine, _empty_tables: Callable[[], Awaitable[None]]) -> None:
    await _empty_tables()


@pytest.fixture(autouse=True)
def _dirty_writer_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """SPANLIGHT_TEST_WRITE_SHARDS=1 runs the suite with release-2 sharded dirty writes."""
    if os.environ.get("SPANLIGHT_TEST_WRITE_SHARDS") == "1":
        monkeypatch.setattr(dirty, "WRITE_SHARDS", True)


@pytest.fixture
def email_settings(settings: Settings, tmp_path: Path) -> Settings:
    """Settings with email configured: the console provider writing to a file in `tmp_path`."""
    return settings.model_copy(update={"email_console_file": tmp_path / "outbox.jsonl"})


@asynccontextmanager
async def _running(settings: Settings) -> AsyncIterator[Any]:
    application = create_app(settings)
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def app(settings: Settings) -> AsyncIterator[Any]:
    async with _running(settings) as application:
        yield application


@pytest.fixture
async def email_app(email_settings: Settings) -> AsyncIterator[Any]:
    """The app with email configured, for flows that send mail."""
    async with _running(email_settings) as application:
        yield application


@pytest.fixture
async def client(app: Any) -> AsyncIterator[httpx.AsyncClient]:
    async with _make_client(app) as http:
        yield http


@pytest.fixture
async def bare_client(app: Any) -> AsyncIterator[httpx.AsyncClient]:
    """A client with no default headers: no `Origin`, and no cookies of its own."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=TEST_ORIGIN) as http:
        yield http


@pytest.fixture
async def email_client(email_app: Any) -> AsyncIterator[httpx.AsyncClient]:
    async with _make_client(email_app) as http:
        yield http


def _make_client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url=TEST_ORIGIN,
        headers={"Origin": TEST_ORIGIN},
    )


BrowserFactory = Callable[..., Awaitable[Browser]]


@asynccontextmanager
async def _browsers(app: Any) -> AsyncIterator[BrowserFactory]:
    clients: list[httpx.AsyncClient] = []

    async def factory(email: str, name: str = "Test User") -> Browser:
        http = _make_client(app)
        clients.append(http)
        response = await http.post(
            "/api/v1/auth/signup",
            json={"email": email, "password": "correct horse battery", "name": name},
        )
        assert response.status_code == 201, response.text
        return Browser(http=http, user=response.json())

    try:
        yield factory
    finally:
        for http in clients:
            await http.aclose()


@pytest.fixture
async def browser_factory(app: Any) -> AsyncIterator[BrowserFactory]:
    """`await browser_factory("ada@example.com")` → a signed-up, signed-in Browser."""
    async with _browsers(app) as factory:
        yield factory


@pytest.fixture
async def email_browser_factory(email_app: Any) -> AsyncIterator[BrowserFactory]:
    """Like `browser_factory`, against the app with email configured."""
    async with _browsers(email_app) as factory:
        yield factory
