"""`spanlight doctor precision`: the precision table across every project, or one organization."""

import uuid
from collections.abc import Iterator

import psycopg
import pytest
from typer.testing import CliRunner

from app import cli
from app.config import get_settings

HEADER = "| kind | right | wrong | n | precision | wilson_low | wilson_high | meets_target |"


@pytest.fixture
def runner(monkeypatch: pytest.MonkeyPatch, app_database_url: str) -> Iterator[CliRunner]:
    monkeypatch.setenv("DATABASE_URL", app_database_url)
    get_settings.cache_clear()
    yield CliRunner()
    get_settings.cache_clear()


def _connect(app_database_url: str) -> psycopg.Connection[tuple[object, ...]]:
    return psycopg.connect(app_database_url.replace("postgresql+psycopg://", "postgresql://"))


def _seed_org(app_database_url: str, name: str) -> uuid.UUID:
    org_id, project_id = uuid.uuid4(), uuid.uuid4()
    with _connect(app_database_url) as conn:
        conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES (%s, %s, %s)",
            (org_id, name, name.lower()),
        )
        conn.execute(
            "INSERT INTO projects (id, org_id, name, slug) VALUES (%s, %s, 'App', 'app')",
            (project_id, org_id),
        )
    return project_id


def _seed_votes(
    app_database_url: str, project_id: uuid.UUID, kind: str, right: int, wrong: int
) -> uuid.UUID:
    """One insight of `kind` with `right` and `wrong` votes, each from a new user."""
    insight_id = uuid.uuid4()
    with _connect(app_database_url) as conn:
        conn.execute("SELECT set_config('app.bypass_rls', 'on', true)")
        conn.execute(
            "INSERT INTO insights (id, project_id, kind, severity, status, fingerprint, title, "
            "summary, failure_layer, certainty, evidence, suggested_fix, verification, "
            "first_seen_at, last_seen_at, occurrences) VALUES (%s, %s, %s, 'warning', 'open', "
            "%s, 't', 's', 'provider', 'measured', '{}', 'f', 'v', now(), now(), 1)",
            (insight_id, project_id, kind, insight_id.hex),
        )
        for verdict in ["right"] * right + ["wrong"] * wrong:
            user_id = uuid.uuid4()
            conn.execute(
                "INSERT INTO users (id, email, name) VALUES (%s, %s, 'Voter')",
                (user_id, f"{user_id.hex}@example.com"),
            )
            conn.execute(
                "INSERT INTO insight_feedback (id, project_id, insight_id, user_id, verdict) "
                "VALUES (gen_random_uuid(), %s, %s, %s, %s)",
                (project_id, insight_id, user_id, verdict),
            )
    return insight_id


def _org_of(app_database_url: str, project_id: uuid.UUID) -> uuid.UUID:
    with _connect(app_database_url) as conn:
        row = conn.execute("SELECT org_id FROM projects WHERE id = %s", (project_id,)).fetchone()
    assert row is not None
    org_id = row[0]
    assert isinstance(org_id, uuid.UUID)
    return org_id


def _rows(output: str) -> dict[str, list[str]]:
    """The table's body rows by kind, cells stripped."""
    lines = [line for line in output.splitlines() if line.startswith("|")]
    assert lines[0] == HEADER
    cells = [[cell.strip() for cell in line.strip("|").split("|")] for line in lines[2:]]
    return {row[0]: row[1:] for row in cells}


@pytest.fixture
def two_orgs(app_database_url: str) -> tuple[uuid.UUID, uuid.UUID, list[uuid.UUID]]:
    acme = _seed_org(app_database_url, "Acme")
    globex = _seed_org(app_database_url, "Globex")
    insights = [
        _seed_votes(app_database_url, acme, "error_spike", right=18, wrong=2),
        _seed_votes(app_database_url, acme, "retired_detector", right=1, wrong=0),
        _seed_votes(app_database_url, globex, "error_spike", right=1, wrong=1),
        _seed_votes(app_database_url, globex, "retry_storm", right=3, wrong=2),
    ]
    return _org_of(app_database_url, acme), _org_of(app_database_url, globex), insights


def test_one_org_prints_only_its_kinds(
    runner: CliRunner, two_orgs: tuple[uuid.UUID, uuid.UUID, list[uuid.UUID]]
) -> None:
    acme, _, _ = two_orgs
    result = runner.invoke(cli.app, ["doctor", "precision", "--org", str(acme)])
    assert result.exit_code == 0, result.output
    assert _rows(result.output) == {
        "error_spike": ["18", "2", "20", "0.9000", "0.6990", "0.9721", "yes"],
        "retired_detector": ["1", "0", "1", "1.0000", "0.2065", "1.0000", "no"],
    }


def test_without_an_org_every_project_is_summed(
    runner: CliRunner, two_orgs: tuple[uuid.UUID, uuid.UUID, list[uuid.UUID]]
) -> None:
    result = runner.invoke(cli.app, ["doctor", "precision"])
    assert result.exit_code == 0, result.output
    rows = _rows(result.output)
    assert sorted(rows) == ["error_spike", "retired_detector", "retry_storm"]
    assert rows["error_spike"][:3] == ["19", "3", "22"]
    assert rows["retry_storm"] == ["3", "2", "5", "0.6000", "0.2307", "0.8824", "no"]


def test_the_output_holds_aggregates_only(
    runner: CliRunner, two_orgs: tuple[uuid.UUID, uuid.UUID, list[uuid.UUID]]
) -> None:
    acme, globex, insights = two_orgs
    result = runner.invoke(cli.app, ["doctor", "precision"])
    assert result.exit_code == 0, result.output
    for identifier in [acme, globex, *insights]:
        assert str(identifier) not in result.output
        assert identifier.hex not in result.output


def test_a_kind_without_votes_shows_dashes(runner: CliRunner, app_database_url: str) -> None:
    project_id = _seed_org(app_database_url, "Acme")
    _seed_votes(app_database_url, project_id, "retry_storm", right=0, wrong=0)
    result = runner.invoke(cli.app, ["doctor", "precision"])
    assert result.exit_code == 0, result.output
    assert _rows(result.output) == {"retry_storm": ["0", "0", "0", "—", "—", "—", "no"]}


def test_an_unknown_org_fails(runner: CliRunner) -> None:
    result = runner.invoke(cli.app, ["doctor", "precision", "--org", str(uuid.uuid4())])
    assert result.exit_code != 0
    assert "no organization" in result.output
