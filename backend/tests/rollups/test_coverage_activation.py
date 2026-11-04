"""Administrative activation distinguishes uncertain legacy history from new project IDs."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from itertools import count
from threading import Barrier
from time import monotonic
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import psycopg
import pytest
from typer.testing import CliRunner

from alembic import command
from app import cli
from app.config import get_settings
from app.db.migrations import alembic_config, downgrade_to
from app.rollups import activation
from app.rollups.activation import NonemptyInventoryError, activate_aggregate_coverage
from tests.conftest import _base_database_url, _libpq
from tests.database_lifecycle import owned_database
from tests.helpers import create_workspace


def test_legacy_activation_is_conservative_and_fresh_mode_refuses_existing_projects(
    database_server: None,
) -> None:
    name = f"spanlight_test_{uuid4().hex}_main"
    base = _libpq(_base_database_url())
    url = urlunsplit(urlsplit(base)._replace(path=f"/{name}"))
    with owned_database(base, name):
        command.upgrade(alembic_config(url), "1027")
        project = uuid4()
        with psycopg.connect(_libpq(url)) as db:
            db.execute(
                "INSERT INTO organizations (id, name, slug) VALUES (%s, 'Org', 'org')", (project,)
            )
            db.execute(
                "INSERT INTO projects (id, org_id, name, slug) VALUES (%s, %s, 'P', 'p')",
                (project, project),
            )
            assert db.execute(
                "SELECT aggregate_history_unverified_before FROM projects"
            ).fetchone() == (None,)
        with pytest.raises(ValueError, match="empty project inventory"):
            activate_aggregate_coverage(url, fresh_install=True)
        before = datetime.now(UTC) + timedelta(minutes=10)
        boundary = activate_aggregate_coverage(url)
        assert boundary > before
        assert boundary.hour == boundary.minute == boundary.second == boundary.microsecond == 0
        with psycopg.connect(_libpq(url)) as db:
            assert db.execute(
                "SELECT aggregate_history_unverified_before FROM projects"
            ).fetchone() == (boundary,)
            db.execute(
                "INSERT INTO projects (id, org_id, name, slug) VALUES (%s, %s, 'New', 'new')",
                (uuid4(), project),
            )
            assert db.execute(
                "SELECT aggregate_history_unverified_before FROM projects WHERE slug = 'new'"
            ).fetchone() == (datetime(1970, 1, 1, tzinfo=UTC),)
        with pytest.raises(Exception, match="coverage state is required"):
            downgrade_to(url, "1026")


async def test_fresh_activation_keeps_ordinary_project_create_and_get_working(
    browser_factory: Any,
) -> None:
    workspace = await create_workspace(await browser_factory("new-project@example.com"))
    response = await workspace.owner.get(f"/api/v1/projects/{workspace.project_id}")
    assert response.status_code == 200, response.text
    assert response.json()["id"] == workspace.project_id


def test_proven_new_projects_allow_rollback_only_without_missing_source_evidence(
    database_server: None,
) -> None:
    name = f"spanlight_test_{uuid4().hex}_main"
    base = _libpq(_base_database_url())
    url = urlunsplit(urlsplit(base)._replace(path=f"/{name}"))
    with owned_database(base, name):
        command.upgrade(alembic_config(url), "1027")
        activate_aggregate_coverage(url, fresh_install=True)
        project = uuid4()
        with psycopg.connect(url) as db:
            db.execute(
                "INSERT INTO organizations (id, name, slug) VALUES (%s, 'O', 'o')", (project,)
            )
            db.execute(
                "INSERT INTO projects (id, org_id, name, slug) VALUES (%s, %s, 'P', 'p')",
                (project, project),
            )
        downgrade_to(url, "1026")
        command.upgrade(alembic_config(url), "1027")
        with psycopg.connect(url) as db:
            db.execute("UPDATE projects SET aggregate_history_unverified_before = '1970-01-01Z'")
            db.execute(
                "INSERT INTO aggregate_incomplete_hours (project_id, bucket_hour, reason) "
                "VALUES (%s, '2026-09-01Z', 'retention')",
                (project,),
            )
        with pytest.raises(Exception, match="coverage state is required"):
            downgrade_to(url, "1026")


def test_compose_first_run_activates_an_empty_inventory_and_skips_one_with_projects(
    database_server: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    name = f"spanlight_test_{uuid4().hex}_main"
    base = _libpq(_base_database_url())
    url = urlunsplit(urlsplit(base)._replace(path=f"/{name}"))
    first_run = [
        "activate-aggregate-coverage",
        "--writers-drained",
        "--fresh-install",
        "--skip-if-projects-exist",
    ]
    with owned_database(base, name):
        command.upgrade(alembic_config(url), "1027")
        monkeypatch.setenv("DATABASE_URL", url)
        get_settings.cache_clear()
        try:
            result = CliRunner().invoke(cli.app, first_run)
            assert result.exit_code == 0, result.output
            assert "Coverage activated" in result.output
            org = uuid4()
            with psycopg.connect(url) as db:
                db.execute(
                    "INSERT INTO organizations (id, name, slug) VALUES (%s, 'O', 'o')", (org,)
                )
                db.execute(
                    "INSERT INTO projects (id, org_id, name, slug) VALUES (%s, %s, 'P', 'p')",
                    (uuid4(), org),
                )
            # Every later `up` runs the same step: it must neither fail nor touch the projects.
            result = CliRunner().invoke(cli.app, first_run)
            assert result.exit_code == 0, result.output
            assert "skipped" in result.output
            # Without the flag the refusal stays an error.
            refused = CliRunner().invoke(cli.app, first_run[:-1])
            assert refused.exit_code != 0
            assert isinstance(refused.exception, NonemptyInventoryError)
        finally:
            get_settings.cache_clear()
        with psycopg.connect(url) as db:
            assert db.execute(
                "SELECT aggregate_history_unverified_before FROM projects"
            ).fetchone() == (datetime(1970, 1, 1, tzinfo=UTC),)


def test_fresh_activation_refuses_a_live_inventory_without_waiting_for_its_lock(
    database_server: None,
) -> None:
    name = f"spanlight_test_{uuid4().hex}_main"
    base = _libpq(_base_database_url())
    url = urlunsplit(urlsplit(base)._replace(path=f"/{name}"))
    with owned_database(base, name):
        command.upgrade(alembic_config(url), "1027")
        # An empty install activates on every Compose `up`, again and again, without error.
        activate_aggregate_coverage(url, fresh_install=True)
        activate_aggregate_coverage(url, fresh_install=True)
        org = uuid4()
        with psycopg.connect(url) as db:
            db.execute("INSERT INTO organizations (id, name, slug) VALUES (%s, 'O', 'o')", (org,))
            db.execute(
                "INSERT INTO projects (id, org_id, name, slug) VALUES (%s, %s, 'P', 'p')",
                (uuid4(), org),
            )
        # A running api or worker holds project rows the way ingestion and rollups do.
        with psycopg.connect(url) as live:
            live.execute("SELECT id FROM projects FOR KEY SHARE")
            started = monotonic()
            with pytest.raises(NonemptyInventoryError):
                activate_aggregate_coverage(url, fresh_install=True)
            assert monotonic() - started < 2


def test_two_concurrent_fresh_activations_of_an_empty_inventory_both_succeed(
    database_server: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    name = f"spanlight_test_{uuid4().hex}_main"
    base = _libpq(_base_database_url())
    url = urlunsplit(urlsplit(base)._replace(path=f"/{name}"))
    # Both runs finish the unlocked check before either asks for the lock, the way two Compose
    # `up`s can meet; only those first two checks wait for each other.
    met, calls, has_projects = Barrier(2, timeout=10), count(), activation._has_projects

    def checked(connection: psycopg.Connection[tuple[object, ...]]) -> bool:
        found = has_projects(connection)
        if next(calls) < 2:
            met.wait()
        return found

    monkeypatch.setattr(activation, "_has_projects", checked)
    with owned_database(base, name):
        command.upgrade(alembic_config(url), "1027")
        with ThreadPoolExecutor(2) as pool:
            runs = [
                pool.submit(activate_aggregate_coverage, url, fresh_install=True) for _ in range(2)
            ]
            boundaries = [run.result(timeout=20) for run in runs]
        assert len(boundaries) == 2
