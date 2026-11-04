"""`spanlight set-ingest-caps`: set, print and clear one project's ingest cap overrides."""

import uuid
from collections.abc import Iterator

import psycopg
import pytest
from typer.testing import CliRunner

from app import cli
from app.config import get_settings


@pytest.fixture
def runner(monkeypatch: pytest.MonkeyPatch, app_database_url: str) -> Iterator[CliRunner]:
    monkeypatch.setenv("DATABASE_URL", app_database_url)
    monkeypatch.setenv("INGEST_SPANS_PER_DAY", "0")
    get_settings.cache_clear()
    yield CliRunner()
    get_settings.cache_clear()


def _connect(app_database_url: str) -> psycopg.Connection[tuple[object, ...]]:
    return psycopg.connect(app_database_url.replace("postgresql+psycopg://", "postgresql://"))


def _seed_project(app_database_url: str) -> uuid.UUID:
    org_id, project_id = uuid.uuid4(), uuid.uuid4()
    with _connect(app_database_url) as conn:
        conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES (%s, 'Acme', %s)",
            (org_id, f"acme-{org_id.hex[:8]}"),
        )
        conn.execute(
            "INSERT INTO projects (id, org_id, name, slug) VALUES (%s, %s, 'App', 'app')",
            (project_id, org_id),
        )
    return project_id


def _overrides(app_database_url: str, project_id: uuid.UUID) -> tuple[object, ...]:
    with _connect(app_database_url) as conn:
        row = conn.execute(
            "SELECT ingest_spans_per_second, ingest_spans_per_day, ingest_bytes_per_day "
            "FROM projects WHERE id = %s",
            (project_id,),
        ).fetchone()
    assert row is not None
    return row


def test_set_print_and_clear_overrides(runner: CliRunner, app_database_url: str) -> None:
    project_id = _seed_project(app_database_url)

    result = runner.invoke(
        cli.app,
        [
            "set-ingest-caps",
            str(project_id),
            "--spans-per-day",
            "5000000",
            "--spans-per-second",
            "20",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "spans_per_second: 20 (project)" in result.output
    assert "spans_per_day: 5,000,000 (project)" in result.output
    assert "bytes_per_day: 5,368,709,120 (default)" in result.output
    assert _overrides(app_database_url, project_id) == (20, 5_000_000, None)

    cleared = runner.invoke(
        cli.app, ["set-ingest-caps", str(project_id), "--spans-per-day", "default"]
    )

    assert cleared.exit_code == 0, cleared.output
    assert "spans_per_day: unlimited (default)" in cleared.output
    assert _overrides(app_database_url, project_id) == (20, None, None)


@pytest.mark.parametrize("value", ["0", "-5", "many"])
def test_a_cap_must_be_a_positive_integer_or_default(
    runner: CliRunner, app_database_url: str, value: str
) -> None:
    project_id = _seed_project(app_database_url)

    result = runner.invoke(cli.app, ["set-ingest-caps", str(project_id), "--bytes-per-day", value])

    assert result.exit_code != 0
    assert _overrides(app_database_url, project_id) == (None, None, None)


def test_an_unknown_project_fails(runner: CliRunner) -> None:
    result = runner.invoke(cli.app, ["set-ingest-caps", str(uuid.uuid4()), "--spans-per-day", "1"])

    assert result.exit_code != 0
    assert "no project with id" in result.output
