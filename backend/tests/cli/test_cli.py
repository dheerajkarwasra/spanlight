"""The `spanlight` admin CLI against the test database."""

from collections.abc import Iterator

import click
import psycopg
import pytest
from typer.testing import CliRunner

from app import cli
from app.config import get_settings
from app.rollups.recovery import RecentRecoveryError

PASSWORD = "brand new password"


@pytest.fixture
def runner(monkeypatch: pytest.MonkeyPatch, app_database_url: str) -> Iterator[CliRunner]:
    monkeypatch.setenv("DATABASE_URL", app_database_url)
    get_settings.cache_clear()
    yield CliRunner()
    get_settings.cache_clear()


def _query(app_database_url: str, sql: str) -> list[tuple[object, ...]]:
    with psycopg.connect(
        app_database_url.replace("postgresql+psycopg://", "postgresql://")
    ) as conn:
        return conn.execute(sql).fetchall()


def _seed_org(app_database_url: str) -> None:
    with psycopg.connect(
        app_database_url.replace("postgresql+psycopg://", "postgresql://")
    ) as conn:
        conn.execute(
            "INSERT INTO organizations (id, name, slug) "
            "VALUES ('00000000-0000-7000-8000-000000000001', 'Acme', 'acme')"
        )


def test_create_user_with_org_writes_audit_event(runner: CliRunner, app_database_url: str) -> None:
    _seed_org(app_database_url)
    result = runner.invoke(
        cli.app,
        [
            "create-user",
            "--email",
            "ops@example.com",
            "--name",
            "Ops",
            "--org",
            "acme",
            "--role",
            "admin",
        ],
        input=f"{PASSWORD}\n{PASSWORD}\n",
    )
    assert result.exit_code == 0, result.output
    assert "Created user" in result.output

    assert _query(app_database_url, "SELECT role::text FROM memberships") == [("admin",)]
    assert _query(app_database_url, "SELECT action FROM audit_events") == [("member.add",)]


def test_create_user_rejects_short_password(runner: CliRunner, app_database_url: str) -> None:
    result = runner.invoke(
        cli.app,
        ["create-user", "--email", "ops@example.com", "--name", "Ops"],
        input="short\nshort\n",
    )
    assert result.exit_code != 0
    assert _query(app_database_url, "SELECT count(*) FROM users") == [(0,)]


def test_reset_password_revokes_sessions_and_audits(
    runner: CliRunner, app_database_url: str
) -> None:
    _seed_org(app_database_url)
    created = runner.invoke(
        cli.app,
        ["create-user", "--email", "ops@example.com", "--name", "Ops", "--org", "acme"],
        input=f"{PASSWORD}\n{PASSWORD}\n",
    )
    assert created.exit_code == 0, created.output

    result = runner.invoke(
        cli.app,
        ["reset-password", "--email", "OPS@example.com"],
        input="another password!\nanother password!\n",
    )
    assert result.exit_code == 0, result.output
    actions = _query(app_database_url, "SELECT action FROM audit_events ORDER BY created_at")
    assert actions == [("member.add",), ("user.password_reset",)]


def test_reset_password_unknown_user_fails(runner: CliRunner) -> None:
    result = runner.invoke(
        cli.app,
        ["reset-password", "--email", "ghost@example.com"],
        input=f"{PASSWORD}\n{PASSWORD}\n",
    )
    assert result.exit_code != 0


# --- reset-2fa ---------------------------------------------------------------------------------


def _enrol_with_two_factor(app_database_url: str, email: str) -> None:
    """Put a user into the state `enable` leaves: a sealed secret, a step and recovery codes."""
    with psycopg.connect(
        app_database_url.replace("postgresql+psycopg://", "postgresql://")
    ) as conn:
        conn.execute(
            "UPDATE users SET totp_secret = '\\x0102', totp_key_id = 'k1', "
            "totp_enabled_at = now(), totp_last_step = 59000000 WHERE email = %s",
            (email,),
        )
        conn.execute(
            # Every stored format: NULL (before code_length existed), 10 and 28.
            "INSERT INTO recovery_codes (user_id, code_hash, code_length) "
            "SELECT id, sha256(convert_to(n::text, 'UTF8')), "
            "(ARRAY[NULL, 10, 28]::smallint[])[n %% 3 + 1] "
            "FROM users, generate_series(1, 10) n WHERE email = %s",
            (email,),
        )


def _create_ops_user(
    runner: CliRunner, app_database_url: str, email: str = "ops@example.com"
) -> None:
    created = runner.invoke(
        cli.app,
        ["create-user", "--email", email, "--name", "Ops", "--org", "acme"],
        input=f"{PASSWORD}\n{PASSWORD}\n",
    )
    assert created.exit_code == 0, created.output


def test_reset_2fa_turns_it_off_deletes_the_recovery_codes_and_audits(
    runner: CliRunner, app_database_url: str
) -> None:
    _seed_org(app_database_url)
    _create_ops_user(runner, app_database_url)
    _enrol_with_two_factor(app_database_url, "ops@example.com")

    result = runner.invoke(cli.app, ["reset-2fa", "--email", "OPS@example.com"])

    assert result.exit_code == 0, result.output
    assert "ops@example.com" in result.output.lower()
    assert len(result.output.strip().splitlines()) == 1
    assert _query(
        app_database_url,
        "SELECT totp_secret, totp_key_id, totp_enabled_at, totp_last_step FROM users",
    ) == [(None, None, None, None)]
    assert _query(app_database_url, "SELECT count(*) FROM recovery_codes") == [(0,)]
    assert _query(
        app_database_url,
        "SELECT action, actor_user_id IS NULL, target_type, metadata->>'via' "
        "FROM audit_events WHERE action LIKE 'user.totp_%'",
    ) == [("user.totp_disable", True, "user", "cli")]


def test_reset_2fa_audits_in_each_org_of_the_user(runner: CliRunner, app_database_url: str) -> None:
    _seed_org(app_database_url)
    with psycopg.connect(
        app_database_url.replace("postgresql+psycopg://", "postgresql://")
    ) as conn:
        conn.execute(
            "INSERT INTO organizations (id, name, slug) "
            "VALUES ('00000000-0000-7000-8000-000000000002', 'Globex', 'globex')"
        )
    _create_ops_user(runner, app_database_url)
    with psycopg.connect(
        app_database_url.replace("postgresql+psycopg://", "postgresql://")
    ) as conn:
        conn.execute(
            "INSERT INTO memberships (org_id, user_id, role) "
            "SELECT '00000000-0000-7000-8000-000000000002', id, 'member' FROM users"
        )
    _enrol_with_two_factor(app_database_url, "ops@example.com")

    result = runner.invoke(cli.app, ["reset-2fa", "--email", "ops@example.com"])

    assert result.exit_code == 0, result.output
    assert _query(
        app_database_url,
        "SELECT o.slug FROM audit_events a JOIN organizations o ON o.id = a.org_id "
        "WHERE a.action = 'user.totp_disable' ORDER BY o.slug",
    ) == [("acme",), ("globex",)]


def test_reset_2fa_leaves_other_users_alone(runner: CliRunner, app_database_url: str) -> None:
    _seed_org(app_database_url)
    _create_ops_user(runner, app_database_url, "ops@example.com")
    _create_ops_user(runner, app_database_url, "other@example.com")
    _enrol_with_two_factor(app_database_url, "ops@example.com")
    _enrol_with_two_factor(app_database_url, "other@example.com")

    result = runner.invoke(cli.app, ["reset-2fa", "--email", "ops@example.com"])

    assert result.exit_code == 0, result.output
    assert _query(
        app_database_url,
        "SELECT email, totp_enabled_at IS NOT NULL FROM users ORDER BY email",
    ) == [("ops@example.com", False), ("other@example.com", True)]
    assert _query(app_database_url, "SELECT count(*) FROM recovery_codes") == [(10,)]


def test_reset_2fa_unknown_user_fails_and_changes_nothing(
    runner: CliRunner, app_database_url: str
) -> None:
    _seed_org(app_database_url)
    _create_ops_user(runner, app_database_url)
    _enrol_with_two_factor(app_database_url, "ops@example.com")

    result = runner.invoke(cli.app, ["reset-2fa", "--email", "ghost@example.com"])

    assert result.exit_code != 0
    assert "ghost@example.com" in result.output
    assert _query(app_database_url, "SELECT totp_enabled_at IS NOT NULL FROM users") == [(True,)]
    assert _query(app_database_url, "SELECT count(*) FROM recovery_codes") == [(10,)]
    assert _query(
        app_database_url, "SELECT count(*) FROM audit_events WHERE action LIKE 'user.totp_%'"
    ) == [(0,)]


def test_reset_2fa_for_a_user_without_it_says_so_and_writes_nothing(
    runner: CliRunner, app_database_url: str
) -> None:
    _seed_org(app_database_url)
    _create_ops_user(runner, app_database_url)

    result = runner.invoke(cli.app, ["reset-2fa", "--email", "ops@example.com"])

    assert result.exit_code == 0, result.output
    assert "not" in result.output.lower()
    assert _query(
        app_database_url, "SELECT count(*) FROM audit_events WHERE action LIKE 'user.totp_%'"
    ) == [(0,)]


def test_reset_2fa_keeps_sessions_and_the_password(
    runner: CliRunner, app_database_url: str
) -> None:
    # Only the second factor is touched; signing the user out everywhere is `reset-password`.
    _seed_org(app_database_url)
    _create_ops_user(runner, app_database_url)
    _enrol_with_two_factor(app_database_url, "ops@example.com")
    before = _query(app_database_url, "SELECT password_hash FROM users")
    with psycopg.connect(
        app_database_url.replace("postgresql+psycopg://", "postgresql://")
    ) as conn:
        conn.execute(
            "INSERT INTO sessions (id, user_id, token_hash, last_seen_at, expires_at, "
            "absolute_expires_at) SELECT gen_random_uuid(), id, '\\x01', now(), "
            "now() + interval '1 day', now() + interval '1 day' FROM users"
        )

    runner.invoke(cli.app, ["reset-2fa", "--email", "ops@example.com"])

    assert _query(app_database_url, "SELECT password_hash FROM users") == before
    assert _query(app_database_url, "SELECT count(*) FROM sessions") == [(1,)]


def test_aggregate_activation_requires_explicit_drain_acknowledgement(runner: CliRunner) -> None:
    result = runner.invoke(cli.app, ["activate-aggregate-coverage"])
    assert result.exit_code == 2
    # Rich forces colour under GITHUB_ACTIONS, and its styling splits the option name.
    assert "--writers-drained" in click.unstyle(result.output)


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        (TimeoutError(), "stopped at its time limit"),
        (RecentRecoveryError("Project was deleted during recent recovery"), "Project was deleted"),
    ],
)
def test_recent_recovery_failure_is_an_error_line_not_a_traceback(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, failure: Exception, message: str
) -> None:
    async def fail(*_: object) -> None:
        raise failure

    monkeypatch.setattr(cli, "recover_recent", fail)
    result = runner.invoke(
        cli.app,
        [
            "rollups",
            "backfill",
            "--project",
            "00000000-0000-7000-8000-000000000002",
            "--from",
            "2026-10-01T00:00Z",
            "--to",
            "2026-10-01T01:00Z",
            "--resume-recent",
        ],
    )
    assert isinstance(result.exception, SystemExit), result.exception
    assert result.exit_code == 1
    assert message in result.output


def test_an_unexpected_recovery_bug_keeps_its_traceback(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    bug = RuntimeError("unexpected")

    async def fail(*_: object) -> None:
        raise bug

    monkeypatch.setattr(cli, "recover_recent", fail)
    result = runner.invoke(
        cli.app,
        [
            "rollups",
            "backfill",
            "--project",
            "00000000-0000-7000-8000-000000000002",
            "--from",
            "2026-10-01T00:00Z",
            "--to",
            "2026-10-01T01:00Z",
            "--resume-recent",
        ],
    )
    assert result.exception is bug
