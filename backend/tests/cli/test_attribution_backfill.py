"""`spanlight attribution backfill`: recompute one project's attribution rollups over a range."""

import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

import psycopg
import pytest
from typer.testing import CliRunner, Result

from app import cli
from app.config import get_settings

TODAY = datetime.now(UTC).date()
RETENTION_DAYS = 30


@pytest.fixture
def runner(monkeypatch: pytest.MonkeyPatch, app_database_url: str) -> Iterator[CliRunner]:
    monkeypatch.setenv("DATABASE_URL", app_database_url)
    get_settings.cache_clear()
    yield CliRunner()
    get_settings.cache_clear()


def _connect(app_database_url: str) -> psycopg.Connection[tuple[object, ...]]:
    return psycopg.connect(app_database_url.replace("postgresql+psycopg://", "postgresql://"))


def _seed_project(app_database_url: str, days: list[date]) -> uuid.UUID:
    """A project with `customer` promoted and one priced trace (name `answer`) per day."""
    org_id, project_id = uuid.uuid4(), uuid.uuid4()
    with _connect(app_database_url) as conn:
        conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES (%s, 'Acme', %s)",
            (org_id, f"acme-{org_id.hex[:8]}"),
        )
        conn.execute(
            "INSERT INTO projects (id, org_id, name, slug, retention_days, "
            "promoted_metadata_keys) VALUES (%s, %s, 'App', 'app', %s, '{customer}')",
            (project_id, org_id, RETENTION_DAYS),
        )
        conn.execute("SELECT set_config('app.bypass_rls', 'on', true)")
        for day in days:
            started = datetime.combine(day, datetime.min.time(), UTC) + timedelta(hours=12)
            trace_id = uuid.uuid4().hex
            conn.execute(
                "INSERT INTO traces (project_id, trace_id, name, metadata, started_at, ended_at) "
                "VALUES (%s, %s, 'answer', '{\"customer\": \"acme\"}', %s, %s)",
                (project_id, trace_id, started, started),
            )
            conn.execute(
                "INSERT INTO spans (project_id, trace_id, span_id, kind, name, status, "
                "started_at, ended_at, provider, model, input_tokens, output_tokens, cost_usd) "
                "VALUES (%s, %s, %s, 'llm', 'call', 'ok', %s, %s, 'openai', 'model-a', 1, 1, 0.5)",
                (project_id, trace_id, trace_id[:16], started, started),
            )
    return project_id


def _rows(app_database_url: str, project_id: uuid.UUID) -> set[tuple[object, ...]]:
    with _connect(app_database_url) as conn:
        conn.execute("SELECT set_config('app.bypass_rls', 'on', true)")
        rows = conn.execute(
            "SELECT group_kind, group_key, day FROM attribution_rollups_daily "
            "WHERE project_id = %s",
            (project_id,),
        ).fetchall()
    return set(rows)


def _backfill(
    runner: CliRunner, project_id: uuid.UUID, first: date, last: date, *extra: str
) -> Result:
    return runner.invoke(
        cli.app,
        [
            "attribution",
            "backfill",
            "--project",
            str(project_id),
            "--from",
            first.isoformat(),
            "--to",
            last.isoformat(),
            *extra,
        ],
    )


def test_attribution_backfill_command_recomputes_the_range(
    runner: CliRunner, app_database_url: str
) -> None:
    days = [TODAY - timedelta(days=offset) for offset in (12, 11, 10, 2)]
    project_id = _seed_project(app_database_url, days)

    result = _backfill(runner, project_id, days[0], days[2])

    assert result.exit_code == 0, result.output
    assert "Wrote 12 rollup rows" in result.output  # four kinds, three days
    # Every kind of the range, each day: a `value` row for the name and the customer, a `none`
    # row for the user and the release the traces lack. The day outside the range is untouched.
    kinds = (("trace_name", None), ("user", None), ("release", None), ("metadata", "customer"))
    assert _rows(app_database_url, project_id) == {
        (kind, key, day) for day in days[:3] for kind, key in kinds
    }

    only_key = _backfill(runner, project_id, days[3], days[3], "--key", "customer")

    assert only_key.exit_code == 0, only_key.output
    assert "Wrote 1 rollup rows" in only_key.output
    assert ("metadata", "customer", days[3]) in _rows(app_database_url, project_id)
    assert ("trace_name", None, days[3]) not in _rows(app_database_url, project_id)


def test_attribution_backfill_is_bounded_by_the_project_retention(
    runner: CliRunner, app_database_url: str
) -> None:
    project_id = _seed_project(app_database_url, [TODAY])
    too_old = TODAY - timedelta(days=RETENTION_DAYS + 1)

    result = _backfill(runner, project_id, too_old, TODAY)

    assert result.exit_code != 0
    assert "retention" in result.output
    assert _rows(app_database_url, project_id) == set()


def test_attribution_backfill_rejects_a_key_that_is_not_promoted(
    runner: CliRunner, app_database_url: str
) -> None:
    project_id = _seed_project(app_database_url, [TODAY])

    result = _backfill(runner, project_id, TODAY, TODAY, "--key", "feature")

    assert result.exit_code != 0
    assert "promoted" in result.output
    assert _rows(app_database_url, project_id) == set()


def test_attribution_backfill_rejects_an_unknown_project(runner: CliRunner) -> None:
    result = _backfill(runner, uuid.uuid4(), TODAY, TODAY)

    assert result.exit_code != 0
    assert "no project" in result.output
