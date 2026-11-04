"""Historical schema tests use an owned database at an explicit upper revision."""

from collections.abc import AsyncIterator, Iterator
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import psycopg
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.migrations import head_revision
from app.db.session import create_engine, create_session_factory
from tests.conftest import APP_ROLE, _base_database_url, _libpq, admin_database_url
from tests.database_lifecycle import owned_database
from tests.db.migration_support import MigrationTarget, active_target, upgrade_to_target


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "migration_revision(revision): run a historical round trip in an owned database"
    )


@pytest.fixture
def migration_database_url(request: pytest.FixtureRequest, app_database_url: str) -> Iterator[str]:
    """One historical test, one exact upper revision; never modify the application's database."""
    marker = request.node.get_closest_marker("migration_revision")
    if marker is None or len(marker.args) != 1:
        raise ValueError("Historical migration test requires an explicit upper revision")
    revision = str(marker.args[0])
    if revision == "head":
        revision = head_revision() or "head"
    name = f"spanlight_test_{uuid4().hex}_main"
    admin_url = urlunsplit(urlsplit(admin_database_url())._replace(path=f"/{name}"))
    url = urlunsplit(urlsplit(app_database_url)._replace(path=f"/{name}"))
    with owned_database(_libpq(_base_database_url()), name):
        with psycopg.connect(_libpq(admin_url), autocommit=True) as db:
            db.execute("DROP SCHEMA public")
            db.execute(f"CREATE SCHEMA public AUTHORIZATION {APP_ROLE}")
            db.execute(f'GRANT CREATE, CONNECT ON DATABASE "{name}" TO {APP_ROLE}')
        token = active_target.set(MigrationTarget(url, admin_url, revision))
        try:
            upgrade_to_target(url)
            yield url
        finally:
            active_target.reset(token)


@pytest.fixture
async def migration_session_factory(
    migration_database_url: str,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_engine(migration_database_url, pool_size=1)
    try:
        yield create_session_factory(engine)
    finally:
        await engine.dispose()
