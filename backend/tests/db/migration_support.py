"""Bounded routing for historical schema probes on one explicitly owned test database."""

from contextvars import ContextVar
from dataclasses import dataclass

from alembic import command
from app.db.migrations import alembic_config, head_revision
from tests.conftest import admin_database_url as shared_admin_database_url


@dataclass(frozen=True)
class MigrationTarget:
    url: str
    admin_url: str
    revision: str


active_target: ContextVar[MigrationTarget | None] = ContextVar("migration_target", default=None)


def admin_database_url() -> str:
    target = active_target.get()
    return target.admin_url if target else shared_admin_database_url()


def expected_revision() -> str | None:
    target = active_target.get()
    return target.revision if target else head_revision()


def _require_target(database_url: str) -> MigrationTarget:
    target = active_target.get()
    if target is None or database_url != target.url:
        raise ValueError("Migration mutation requires the active owned database URL")
    return target


def upgrade_to_target(database_url: str) -> None:
    target = _require_target(database_url)
    command.upgrade(alembic_config(database_url), target.revision)


def downgrade_to(database_url: str, revision: str) -> None:
    _require_target(database_url)
    command.downgrade(alembic_config(database_url), revision)
