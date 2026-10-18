"""`spanlight recompute-usage-rollups`: flag usage rollups and retotal traces stored before Bet 4.

Traces are seeded by direct SQL with the flag unset and wrong totals, the way rows written
before migration 0900 look, so the command's work is visible in the rows it fixes.
"""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import click
import psycopg
import pytest
from typer.testing import CliRunner, Result

from app import cli
from app.config import get_settings

NOW = datetime.now(UTC)


@pytest.fixture
def runner(monkeypatch: pytest.MonkeyPatch, app_database_url: str) -> Iterator[CliRunner]:
    monkeypatch.setenv("DATABASE_URL", app_database_url)
    get_settings.cache_clear()
    yield CliRunner()
    get_settings.cache_clear()


def _connect(app_database_url: str) -> psycopg.Connection[tuple[object, ...]]:
    conn = psycopg.connect(app_database_url.replace("postgresql+psycopg://", "postgresql://"))
    conn.execute("SELECT set_config('app.bypass_rls', 'on', true)")
    return conn


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


def _seed_trace(app_database_url: str, project_id: uuid.UUID, started: datetime) -> str:
    """A parent over a child, both with usage 10/5 and unflagged; totals wrong (99/99, one span)."""
    trace_id = uuid.uuid4().hex
    with _connect(app_database_url) as conn:
        conn.execute(
            "INSERT INTO traces (project_id, trace_id, name, started_at, ended_at, span_count, "
            "input_tokens, output_tokens) VALUES (%s, %s, 'answer', %s, %s, 1, 99, 99)",
            (project_id, trace_id, started, started),
        )
        for span_id, parent in ((trace_id[:16], None), (trace_id[16:], trace_id[:16])):
            conn.execute(
                "INSERT INTO spans (project_id, trace_id, span_id, parent_span_id, kind, name, "
                "status, started_at, ended_at, provider, model, input_tokens, output_tokens, "
                "usage_rollup) VALUES (%s, %s, %s, %s, 'llm', 'call', 'ok', %s, %s, 'openai', "
                "'model-a', 10, 5, false)",
                (project_id, trace_id, span_id, parent, started, started),
            )
    return trace_id


def _rows(app_database_url: str) -> set[tuple[object, ...]]:
    """Every trace's totals and every span's flag, across projects."""
    with _connect(app_database_url) as conn:
        traces = conn.execute(
            "SELECT trace_id, span_count, input_tokens, output_tokens FROM traces"
        ).fetchall()
        spans = conn.execute("SELECT trace_id, span_id, usage_rollup FROM spans").fetchall()
    return set(traces) | set(spans)


def _fixed(trace_id: str) -> set[tuple[object, ...]]:
    return {
        (trace_id, 2, 10, 5),
        (trace_id, trace_id[:16], True),
        (trace_id, trace_id[16:], False),
    }


def _recompute(runner: CliRunner, *args: str) -> Result:
    return runner.invoke(cli.app, ["recompute-usage-rollups", *args])


def test_recompute_flags_old_traces_and_fixes_totals(
    runner: CliRunner, app_database_url: str
) -> None:
    project_id = _seed_project(app_database_url)
    trace_id = _seed_trace(app_database_url, project_id, NOW - timedelta(days=3))

    result = _recompute(runner, "--since", "30d")

    assert result.exit_code == 0, result.output
    assert "Recomputed 1 traces." in result.output
    assert _rows(app_database_url) == _fixed(trace_id)


def test_since_excludes_older_traces(runner: CliRunner, app_database_url: str) -> None:
    project_id = _seed_project(app_database_url)
    _seed_trace(app_database_url, project_id, NOW - timedelta(days=40))
    before = _rows(app_database_url)

    result = _recompute(runner, "--since", "30d")

    assert result.exit_code == 0, result.output
    assert "Recomputed 0 traces." in result.output
    assert _rows(app_database_url) == before


def test_batches_are_one_project_each(runner: CliRunner, app_database_url: str) -> None:
    traces = [
        _seed_trace(app_database_url, project_id, NOW - timedelta(hours=n))
        for project_id in (_seed_project(app_database_url), _seed_project(app_database_url))
        for n in (1, 2)
    ]

    result = _recompute(runner, "--since", "1d", "--batch-size", "3")

    assert result.exit_code == 0, result.output
    assert "Recomputed 4 traces." in result.output
    assert _rows(app_database_url) == set().union(*(_fixed(trace) for trace in traces))


def test_idempotent_second_run_changes_nothing(runner: CliRunner, app_database_url: str) -> None:
    project_id = _seed_project(app_database_url)
    _seed_trace(app_database_url, project_id, NOW - timedelta(hours=1))
    assert _recompute(runner, "--since", "2h").exit_code == 0
    after_first = _rows(app_database_url)

    result = _recompute(runner, "--since", "2h")

    assert result.exit_code == 0, result.output
    assert "Recomputed 1 traces." in result.output
    assert _rows(app_database_url) == after_first


def test_parse_since_rejects_junk(runner: CliRunner) -> None:
    result = _recompute(runner, "--since", "soon")

    assert result.exit_code == 2
    # Typer forces Rich colours on GitHub Actions, which styles each dash of the option apart.
    assert "--since" in click.unstyle(result.output)
