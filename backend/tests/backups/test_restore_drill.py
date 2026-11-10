"""The restore drill: the real backup job, a real restore and reconciliation on invocation-owned
PostgreSQL 17 and MinIO, timed. Also the recovery guards around it.

Marked `serial`: it drives timed child processes and its printed durations are the runbook's
local RPO/RTO record (`pytest -m serial -s tests/backups/test_restore_drill.py`).
"""

import asyncio
import base64
import json
import os
import shutil
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import click
import psycopg
import pytest
from testcontainers.core.container import DockerContainer
from typer.testing import CliRunner

from app import cli
from app.backups import recovery
from app.backups.jobs import run_backup_database
from app.backups.storage import get_backup_object_store
from app.config import Settings, get_settings
from app.core.crypto import Sealed, decrypt, encrypt
from app.db.migrations import head_revision
from app.db.roles import ensure_app_role
from app.db.session import create_engine, create_session_factory
from app.jobs.context import TaskContext
from app.jobs.outcome import JobOutcome
from app.jobs.queue import claim_next, enqueue
from app.jobs.scheduler import schedule_periodic
from app.notifications.lease import claim as claim_notification
from app.notifications.outbox import retry_failed
from tests.backups.test_isolated_storage import (  # noqa: F401 - fixtures by import
    BACKUPS,
    _settings,
    minio,
    pg17_client,
)
from tests.conftest import APP_PASSWORD, APP_ROLE, admin_database_url

pytestmark = pytest.mark.serial

ORG_A, ORG_B = uuid.UUID(int=0xA1), uuid.UUID(int=0xB1)
PROJECT_A, PROJECT_B = uuid.UUID(int=0xA2), uuid.UUID(int=0xB2)
CHANNEL_A = uuid.UUID(int=0xA3)
PENDING, FAILED, SENT = uuid.UUID(int=0xA4), uuid.UUID(int=0xA5), uuid.UUID(int=0xA6)
EXPERIMENT, EXPERIMENT_RUN = uuid.UUID(int=0xA7), uuid.UUID(int=0xA8)
EVALUATOR, EVALUATOR_RUN = uuid.UUID(int=0xA9), uuid.UUID(int=0xAA)
RULE, USER, CREDENTIAL_V2, CREDENTIAL_V1 = (uuid.UUID(int=n) for n in (0xAB, 0xAC, 0xAD, 0xAE))
KEY_V1 = "v1:" + base64.b64encode(b"\x01" * 32).decode()
KEY_V2 = "v2:" + base64.b64encode(b"\x02" * 32).decode()
KEYRING = f"{KEY_V2},{KEY_V1}"
PRODUCER_KINDS = {
    "demo_traffic",
    "evaluate_alerts",
    "weekly_digest",
    "fetch_billing_sources",
    "poll_provider_status",
    "schedule_experiments",
}
RELEASE_ENV = {
    "EXPERIMENT_SCHEDULES_ENABLED": "false",
    "EVALS_ENABLED": "false",
    "ALERTS_EVALUATION_ENABLED": "false",
    "WEEKLY_DIGEST_ENABLED": "false",
    "DEMO_ENABLED": "false",
    "BILLING_SOURCES_ENABLED": "false",
    "PROVIDER_STATUS_POLLING_ENABLED": "false",
}


def _libpq(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def _with_database(url: str, name: str) -> str:
    return urlunsplit(urlsplit(url)._replace(path=f"/{name}"))


def _with_credentials(url: str, user: str, password: str) -> str:
    parts = urlsplit(url)
    return urlunsplit(parts._replace(netloc=f"{user}:{password}@{parts.hostname}:{parts.port}"))


def _rows(url: str, statement: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    with psycopg.connect(_libpq(url), autocommit=True) as connection:
        cursor = connection.execute(statement, params)
        return cursor.fetchall() if cursor.description else []


def _comment(url: str) -> str | None:
    return _rows(
        url,
        "SELECT shobj_description(oid, 'pg_database') FROM pg_database "
        "WHERE datname = current_database()",
    )[0][0]


@pytest.fixture
def pg17_bins(
    pg17_client: DockerContainer,  # noqa: F811 - the imported fixture
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """`pg_dump`, `pg_restore` and `psql` 17, run inside the owned client container (the host has
    PostgreSQL 16 tools, which cannot read a 17 archive)."""
    docker = shutil.which("docker")
    assert docker is not None
    bins = tmp_path / "pg17"
    bins.mkdir()
    container_id = pg17_client.get_wrapped_container().id
    for tool in ("pg_dump", "pg_restore", "psql"):
        wrapper = bins / tool
        wrapper.write_text(
            "#!/usr/bin/env python3\n"
            "import os, sys\n"
            f"args = [{docker!r}, 'exec', '-i']\n"
            "for name in ('PGPORT', 'PGUSER', 'PGPASSWORD', 'PGDATABASE', 'PGSSLMODE'):\n"
            "    if name in os.environ: args += ['-e', name]\n"
            "args += ['-e', 'PGHOST=host.docker.internal']\n"
            f"args += [{container_id!r}, {tool!r}, *sys.argv[1:]]\n"
            "os.execv(args[0], args)\n"
        )
        wrapper.chmod(0o700)
    monkeypatch.setenv("PATH", f"{bins}{os.pathsep}{os.environ['PATH']}")
    return bins


@pytest.fixture
def fresh_databases(app_database_url: str) -> Iterator[list[str]]:
    """Two newly created, empty databases on the owned server, as the owner; dropped afterwards."""
    admin = admin_database_url()
    worker = urlsplit(admin).path.lstrip("/")
    names = [f"{worker}_restore", f"{worker}_restore2"]
    with psycopg.connect(_libpq(admin), autocommit=True) as connection:
        for name in names:
            connection.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
            connection.execute(f'CREATE DATABASE "{name}"')
    try:
        yield [_with_database(admin, name) for name in names]
    finally:
        with psycopg.connect(_libpq(admin), autocommit=True) as connection:
            for name in names:
                connection.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _seed(url: str) -> dict[str, Any]:
    """Two tenants with traces, sealed secrets under the active and a historical key, audit
    chains, queued/running work, pending and failed notifications, and every producer on."""
    current = Settings(_env_file=None, credentials_keys=KEYRING)
    historical = Settings(_env_file=None, credentials_keys=KEY_V1)
    api_key = encrypt(b"sk-current", settings=current)
    old_key = encrypt(b"sk-historical", settings=historical)
    totp = encrypt(b"totp-seed", settings=historical)
    webhook = encrypt(b"whsec", settings=current)
    stream = encrypt(b"audit-secret", settings=current)
    now = datetime.now(UTC)
    hook = '{"url": "https://example.com/hook"}'
    with psycopg.connect(_libpq(url)) as db:
        db.execute(
            "INSERT INTO organizations (id, name, slug) "
            "VALUES (%s, 'Org A', 'org-a'), (%s, 'Org B', 'org-b')",
            (ORG_A, ORG_B),
        )
        db.execute(
            "INSERT INTO projects (id, org_id, name, slug) "
            "VALUES (%s, %s, 'A', 'a'), (%s, %s, 'B', 'b')",
            (PROJECT_A, ORG_A, PROJECT_B, ORG_B),
        )
        db.execute(
            "INSERT INTO users (id, email, name, password_hash, totp_secret, totp_key_id) "
            "VALUES (%s, 'ops@example.com', 'Ops', 'x', %s, %s)",
            (USER, totp.ciphertext, totp.key_id),
        )
        for project, prefix in ((PROJECT_A, "a"), (PROJECT_B, "b")):
            for index in range(2):
                trace_id = f"{prefix}{index}".ljust(32, "0")
                db.execute(
                    "INSERT INTO traces (project_id, trace_id, started_at, ended_at) "
                    "VALUES (%s, %s, %s, %s)",
                    (project, trace_id, now, now),
                )
                db.execute(
                    "INSERT INTO spans (project_id, trace_id, span_id, kind, name, status, "
                    "started_at, ended_at) "
                    "VALUES (%s, %s, %s, 'llm', 'call', 'ok', %s, %s)",
                    (project, trace_id, f"{prefix}{index}".ljust(16, "0"), now, now),
                )
        db.execute(
            "INSERT INTO provider_credentials (id, org_id, provider, name, ciphertext, key_id) "
            "VALUES "
            "(%s, %s, 'openai', 'current', %s, %s), (%s, %s, 'anthropic', 'old', %s, %s)",
            (
                CREDENTIAL_V2,
                ORG_A,
                api_key.ciphertext,
                api_key.key_id,
                CREDENTIAL_V1,
                ORG_B,
                old_key.ciphertext,
                old_key.key_id,
            ),
        )
        db.execute(
            "INSERT INTO alert_channels "
            "(id, org_id, kind, name, config, secret_enc, secret_key_id) "
            "VALUES (%s, %s, 'webhook', 'hook', '{\"url\": \"https://example.com/hook\"}', %s, %s)",
            (CHANNEL_A, ORG_A, webhook.ciphertext, webhook.key_id),
        )
        db.execute(
            "INSERT INTO audit_streams "
            "(org_id, url, secret_enc, secret_key_id, enabled, delivered_seq) "
            "VALUES (%s, 'https://example.com/audit', %s, %s, true, 1)",
            (ORG_A, stream.ciphertext, stream.key_id),
        )
        for org in (ORG_A, ORG_B):
            for _ in range(3):
                db.execute(
                    "INSERT INTO audit_events (id, org_id, action, target_type, target_id) "
                    "VALUES (%s, %s, 'user.totp_enable', 'user', %s)",
                    (uuid.uuid4(), org, USER),
                )
        queued = db.execute(
            "INSERT INTO jobs (kind, payload, status) "
            "VALUES ('run_experiment', '{\"run_id\": \"x\"}', 'queued') RETURNING id"
        ).fetchone()
        running = db.execute(
            "INSERT INTO jobs (kind, status, fence, lease_until, attempts) "
            "VALUES ('deliver_notifications', 'running', 3, now() + interval '1 minute', 1) "
            "RETURNING id"
        ).fetchone()
        done = db.execute(
            "INSERT INTO jobs (kind, status, outcome) "
            "VALUES ('retention', 'done', 'ok') RETURNING id"
        ).fetchone()
        db.execute(
            "INSERT INTO notification_outbox "
            "(id, kind, channel_id, target, payload, status, fence, attempts, last_error, sent_at) "
            "VALUES "
            "(%s, 'alert', %s, %s, '{\"subject\": \"p\"}', 'pending', 0, 0, NULL, NULL), "
            "(%s, 'alert', %s, %s, '{\"subject\": \"f\"}', 'failed', 2, 2, 'boom', NULL), "
            "(%s, 'alert', %s, %s, '{\"subject\": \"s\"}', 'sent', 1, 1, NULL, now())",
            (PENDING, CHANNEL_A, hook, FAILED, CHANNEL_A, hook, SENT, CHANNEL_A, hook),
        )
        dataset = uuid.uuid4()
        db.execute(
            "INSERT INTO datasets (id, project_id, name) VALUES (%s, %s, 'd')", (dataset, PROJECT_A)
        )
        db.execute(
            "INSERT INTO experiments (id, project_id, name, dataset_id, config, schedule) "
            "VALUES (%s, %s, 'e', %s, '{}', 'weekly')",
            (EXPERIMENT, PROJECT_A, dataset),
        )
        db.execute(
            "INSERT INTO experiment_runs (id, experiment_id, number, target, status, heartbeat_at) "
            "VALUES (%s, %s, 1, '{}', 'running', now())",
            (EXPERIMENT_RUN, EXPERIMENT),
        )
        db.execute(
            "INSERT INTO evaluators (id, project_id, name, kind, config, enabled) "
            "VALUES (%s, %s, 'h', 'heuristic', '{\"kind\": \"heuristic\"}', true)",
            (EVALUATOR, PROJECT_A),
        )
        db.execute(
            "INSERT INTO evaluator_runs "
            "(id, evaluator_id, trace_id, status, cost_usd, reserved_usd, heartbeat_at) "
            "VALUES (%s, %s, %s, 'running', 0.1, 0.5, now())",
            (EVALUATOR_RUN, EVALUATOR, "a0".ljust(32, "0")),
        )
        db.execute(
            "INSERT INTO alert_rules "
            "(id, project_id, name, kind, metric, window_minutes, comparator, threshold, enabled) "
            "VALUES (%s, %s, 'r', 'threshold', 'error_rate', 60, 'gt', 0.5, true)",
            (RULE, PROJECT_A),
        )
        db.commit()
    assert queued and running and done
    return {"queued": queued[0], "running": running[0], "done": done[0]}


def _job(url: str, job_id: int) -> tuple[Any, ...]:
    return _rows(
        url,
        "SELECT status::text, fence, last_error, outcome, lease_until FROM jobs WHERE id = %s",
        (job_id,),
    )[0]


def _outbox(url: str, row_id: uuid.UUID) -> tuple[Any, ...]:
    return _rows(
        url,
        "SELECT status::text, fence, last_error, attempts, next_attempt_at, sent_at "
        "FROM notification_outbox WHERE id = %s",
        (row_id,),
    )[0]


def _write(path: Path, document: dict[str, Any]) -> Path:
    path.write_bytes(recovery.canonical_json(document))
    return path


def _decision(entity: str, disposition: str, **extra: Any) -> dict[str, Any]:
    return {
        "entity": entity,
        "disposition": disposition,
        "operator": "ops",
        "decided_at": datetime.now(UTC).isoformat(),
        "reason": "drill",
        **extra,
    }


async def _invoke(arguments: list[str]) -> Any:
    """The CLI runs its own event loop, so it runs off this test's loop."""
    return await asyncio.to_thread(CliRunner().invoke, cli.app, arguments)


def test_reconcile_commands_exist() -> None:
    runner = CliRunner()
    for command in ("prepare", "apply", "check"):
        result = runner.invoke(cli.app, ["restore-reconcile", command, "--help"])
        assert result.exit_code == 0, result.output
        # Rich forces colour under GITHUB_ACTIONS, and its styling splits the option name.
        assert "--all-services-held" in click.unstyle(result.output)


def _receipt(**overrides: Any) -> dict[str, Any]:
    receipt: dict[str, Any] = {
        "format": recovery.RECEIPT_FORMAT,
        "key": "backups/2026/11/01/spanlight-20261101T000000Z.dump",
        "destination": {"bucket": "b", "endpoint": None},
        "sha256": "0" * 64,
        "bytes": 1,
        "target": {"host": "h", "port": "5432", "dbname": "restored"},
        "started_at": "2026-11-01T00:00:00+00:00",
        "finished_at": "2026-11-01T00:00:01+00:00",
        "operator": "ops",
    }
    receipt.update(overrides)
    return receipt


@pytest.mark.parametrize(
    "broken",
    [
        {"target": {"host": "h", "port": "5432"}},
        {"bytes": "1"},
        {"sha256": "nope"},
        {"extra": True},
    ],
    ids=["no-dbname", "bytes-as-string", "bad-sha256", "extra-field"],
)
def test_prepare_refuses_malformed_receipt(tmp_path: Path, broken: dict[str, Any]) -> None:
    """Refused by validation, before any connection: a missing dbname must not bind."""
    receipt = _write(tmp_path / "receipt.json", _receipt(**broken))
    context = _write(
        tmp_path / "context.json",
        {
            "format": recovery.CONTEXT_FORMAT,
            "operator": "ops",
            "target_created_at": "2026-11-01T00:00:00+00:00",
            "target_creation_evidence": "x",
            "backup_started_at": "2026-11-01T00:00:00+00:00",
            "backup_finished_at": "2026-11-01T00:00:01+00:00",
            "isolation_at": "2026-11-01T00:00:02+00:00",
            "release_at": "2026-11-01T01:00:00+00:00",
            "services_held": ["api"],
            "upstream_retries_held": True,
            "windows": [],
            "verification_evidence": [],
        },
    )
    with pytest.raises(recovery.RecoveryError, match=r"receipt\.json is malformed"):
        recovery.prepare(
            "postgresql://nobody:x@localhost:1/none",
            tmp_path / "manifest.json",
            receipt,
            context,
            Settings(_env_file=None),
            APP_ROLE,
            datetime.now(UTC),
        )


async def test_retry_failed_ignores_recovery_marker(
    session_factory: Any, app_database_url: str
) -> None:
    _rows(
        admin_database_url(),
        "INSERT INTO organizations (id, name, slug) VALUES (%s, 'Org A', 'org-a')",
        (ORG_A,),
    )
    _rows(
        admin_database_url(),
        "INSERT INTO alert_channels (id, org_id, kind, name, config) "
        "VALUES (%s, %s, 'email', 'mail', '{\"recipients\": []}')",
        (CHANNEL_A, ORG_A),
    )
    _rows(
        admin_database_url(),
        "INSERT INTO notification_outbox "
        "(id, kind, channel_id, target, payload, status, last_error) VALUES "
        "(%s, 'alert', %s, '{}', '{}', 'failed', 'recovery:abc:suppressed'), "
        "(%s, 'alert', %s, '{}', '{}', 'failed', 'boom')",
        (FAILED, CHANNEL_A, PENDING, CHANNEL_A),
    )
    now = datetime.now(UTC)
    async with session_factory() as db:
        assert await retry_failed(db, ORG_A, CHANNEL_A, FAILED, now) is False
        assert await retry_failed(db, ORG_A, CHANNEL_A, PENDING, now) is True
        await db.rollback()


async def test_restore_drill_end_to_end(
    minio: tuple[DockerContainer, str, Any],  # noqa: F811 - the imported fixture
    app_database_url: str,
    pg17_bins: Path,
    session_factory: Any,
    fresh_databases: list[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, endpoint, admin = minio
    source = admin_database_url()
    target, spare = fresh_databases
    target_name = urlsplit(target).path.lstrip("/")
    ids = _seed(source)
    timings: dict[str, float] = {}

    # 1. The real backup job, with the upload-only principal.
    settings = _settings(endpoint, "upload", backup_database_url=source, credentials_keys=KEYRING)
    backup_started = datetime.now(UTC)
    started = time.monotonic()
    async with session_factory() as db:
        await enqueue(db, "backup_database")
        job = await claim_next(db, supported_kinds={"backup_database"})
        await db.commit()
    assert job is not None
    assert (
        await run_backup_database(TaskContext(job, settings, session_factory), {}) == JobOutcome.OK
    )
    timings["backup_s"] = time.monotonic() - started
    backup_finished = datetime.now(UTC)
    recovery_store = get_backup_object_store(_settings(endpoint, "recovery"))
    assert recovery_store is not None
    listed = await recovery_store.list("backups/")
    key = max(listed, key=lambda info: info.key).key
    dump_bytes = next(info.size for info in listed if info.key == key)
    (source_bytes,) = _rows(source, "SELECT pg_database_size(current_database())")[0]
    isolation_at = datetime.now(UTC)

    # 2. Restore with the recovery principal into a newly created database, with a receipt.
    for name, value in {
        "BACKUP_S3_ENDPOINT": endpoint,
        "BACKUP_S3_BUCKET": BACKUPS,
        "BACKUP_S3_ACCESS_KEY": "recovery",
        "BACKUP_S3_SECRET_KEY": "recovery-secret-password",
        "BACKUP_S3_FORCE_PATH_STYLE": "true",
        "BACKUP_S3_SSE": "",
        "CREDENTIALS_KEYS": KEYRING,
        "RESTORE_TARGET_URL": target,
        **RELEASE_ENV,
    }.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    receipt = tmp_path / "receipt.json"
    try:
        started = time.monotonic()
        restored = await _invoke(["restore", "--key", key, "--receipt", str(receipt)])
        timings["restore_s"] = time.monotonic() - started
        assert restored.exit_code == 0, restored.output
        assert receipt.stat().st_mode & 0o777 == 0o600
        receipt_document = json.loads(receipt.read_bytes())
        assert receipt_document["bytes"] == dump_bytes
        assert receipt_document["target"] == {
            "host": "localhost",
            "port": str(urlsplit(target).port),
            "dbname": target_name,
        }
        assert "password" not in receipt.read_text()

        # A second restore into the same target refuses; so does a target with a relation in
        # another schema; a truncated archive fails with nothing committed.
        again = await _invoke(["restore", "--key", key])
        assert again.exit_code == 1 and "relation(s) outside the system schemas" in again.output
        _rows(spare, "CREATE SCHEMA other; CREATE TABLE other.t (x int)")
        monkeypatch.setenv("RESTORE_TARGET_URL", spare)
        refused = await _invoke(["restore", "--key", key])
        assert refused.exit_code == 1 and "1 relation(s)" in refused.output
        _rows(spare, "DROP SCHEMA other CASCADE")
        body = admin.get_object(Bucket=BACKUPS, Key=key)["Body"].read()
        admin.put_object(Bucket=BACKUPS, Key="backups/truncated.dump", Body=body[: len(body) // 2])
        truncated = await _invoke(["restore", "--key", "backups/truncated.dump"])
        assert truncated.exit_code == 1, truncated.output
        assert _rows(spare, "SELECT count(*) FROM pg_tables WHERE schemaname = 'public'") == [(0,)]
        monkeypatch.setenv("RESTORE_TARGET_URL", target)

        # 3. App role, then verify tenants, RLS, decryption and audit heads on the restored copy.
        ensure_app_role(target, APP_ROLE, APP_PASSWORD)
        target_app = _with_credentials(target, APP_ROLE, APP_PASSWORD)
        assert _rows(target, "SELECT count(*) FROM traces") == [(4,)]
        assert _rows(target, "SELECT count(*) FROM rate_limit_buckets") == [(0,)]
        with psycopg.connect(_libpq(target_app)) as db:
            for project, expected in ((str(PROJECT_A), 2), (str(PROJECT_B), 2), ("", 0)):
                db.execute("SELECT set_config('app.project_id', %s, false)", (project,))
                assert db.execute("SELECT count(*) FROM spans").fetchone() == (expected,)
        ciphertext, key_id = _rows(
            target,
            "SELECT ciphertext, key_id FROM provider_credentials WHERE id = %s",
            (CREDENTIAL_V1,),
        )[0]
        assert (
            decrypt(
                Sealed(bytes(ciphertext), key_id),
                settings=Settings(_env_file=None, credentials_keys=KEYRING),
            )
            == b"sk-historical"
        )

        release_at = datetime.now(UTC) + timedelta(hours=1)
        context = _write(
            tmp_path / "context.json",
            {
                "format": recovery.CONTEXT_FORMAT,
                "operator": "ops",
                "target_created_at": isolation_at.isoformat(),
                "target_creation_evidence": "CREATE DATABASE by the drill fixture",
                "backup_started_at": backup_started.isoformat(),
                "backup_finished_at": backup_finished.isoformat(),
                "isolation_at": isolation_at.isoformat(),
                "release_at": release_at.isoformat(),
                "services_held": ["api", "gateway", "worker", "prune"],
                "upstream_retries_held": True,
                "windows": [
                    {
                        "id": "openai-org-a",
                        "kind": "provider_account",
                        "description": "OpenAI account of Org A",
                        "from_at": backup_started.isoformat(),
                        "to_at": isolation_at.isoformat(),
                    }
                ],
                "verification_evidence": ["this test"],
            },
        )
        manifest = tmp_path / "manifest.json"
        prepare = [
            "restore-reconcile",
            "prepare",
            "--manifest",
            str(manifest),
            "--receipt",
            str(receipt),
            "--context",
            str(context),
            "--app-role",
            APP_ROLE,
        ]
        held = ["--all-services-held"]

        # 4. Prepare refuses without the attestation, without the historical key and on a target
        # the receipt does not name; none of those publishes a manifest or marks the database.
        assert (await _invoke(prepare)).exit_code == 2
        monkeypatch.setenv("CREDENTIALS_KEYS", KEY_V2)
        get_settings.cache_clear()
        missing_key = await _invoke([*prepare, *held])
        assert missing_key.exit_code == 1 and "cannot be opened" in missing_key.output
        monkeypatch.setenv("CREDENTIALS_KEYS", KEYRING)
        monkeypatch.setenv("RESTORE_TARGET_URL", source)
        get_settings.cache_clear()
        wrong_target = await _invoke([*prepare, *held])
        assert wrong_target.exit_code == 1 and "different database" in wrong_target.output
        _rows(
            spare,
            "CREATE TABLE alembic_version (version_num text); "
            f"INSERT INTO alembic_version VALUES ('{head_revision()}')",
        )
        spare_receipt = _write(
            tmp_path / "spare-receipt.json",
            {
                **receipt_document,
                "target": {
                    **receipt_document["target"],
                    "dbname": urlsplit(spare).path.lstrip("/"),
                },
            },
        )
        monkeypatch.setenv("RESTORE_TARGET_URL", spare)
        get_settings.cache_clear()
        broken_schema = await _invoke([*prepare[:5], str(spare_receipt), *prepare[6:], *held])
        assert broken_schema.exit_code == 1 and "database error" in broken_schema.output
        assert "does not exist" in broken_schema.output and "password" not in broken_schema.output
        _rows(spare, "DROP TABLE alembic_version")
        monkeypatch.setenv("RESTORE_TARGET_URL", target)
        get_settings.cache_clear()
        assert not manifest.exists() and _comment(target) is None and _comment(source) is None

        started = time.monotonic()
        prepared = await _invoke([*prepare, *held])
        timings["prepare_s"] = time.monotonic() - started
        assert prepared.exit_code == 0, prepared.output
        document = json.loads(manifest.read_bytes())
        recovery_id = document["recovery_id"]
        manifest_sha = recovery.digest(manifest.read_bytes())
        prepared_marker = (
            f"{recovery.MARKER_PREFIX} id={recovery_id} manifest={manifest_sha} phase=prepared"
        )
        assert _comment(target) == prepared_marker
        unresolved = f"recovery:{recovery_id}:unresolved"
        assert _job(target, ids["queued"])[:4] == ("failed", 1, unresolved, None)
        assert _job(target, ids["running"])[:4] == ("failed", 4, unresolved, None)
        assert _job(target, ids["done"])[:4] == ("done", 0, None, "ok")
        assert _outbox(target, PENDING)[:3] == ("failed", 1, unresolved)
        assert _outbox(target, FAILED)[:3] == ("failed", 3, unresolved)
        assert _outbox(target, SENT)[:3] == ("sent", 1, None)
        assert _rows(
            target,
            "SELECT status, error, heartbeat_at, finished_at IS NOT NULL FROM experiment_runs",
        ) == [("failed", unresolved, None, True)]
        assert _rows(target, "SELECT status, cost_usd, reserved_usd FROM evaluator_runs")[0] == (
            "failed",
            0.5,
            0,
        )
        assert _rows(target, "SELECT count(*) FROM experiments WHERE schedule IS NOT NULL") == [
            (0,)
        ]
        assert (
            _rows(
                target,
                "SELECT bool_or(enabled) FROM evaluators "
                "UNION ALL SELECT bool_or(enabled) FROM alert_rules "
                "UNION ALL SELECT bool_or(weekly_digest_enabled) FROM projects "
                "UNION ALL SELECT bool_or(enabled) FROM audit_streams",
            )
            == [(False,)] * 4
        )
        assert _rows(target, "SELECT delivered_seq, last_error FROM audit_streams") == [
            (1, unresolved)
        ]
        entities = document["entities"]
        assert {e["kind"] for e in entities.values()} >= {
            "job",
            "notification",
            "experiment_run",
            "evaluator_run",
            "experiment",
            "evaluator",
            "alert_rule",
            "project",
            "audit_stream",
            "window",
            "experiment_week",
        }
        assert document["verification"]["sealed"]["provider_credentials"] == {
            "count": 2,
            "key_ids": ["v1", "v2"],
        }
        assert "sk-" not in manifest.read_text() and "whsec" not in manifest.read_text()

        # Prepare again: nothing changes, no second fence bump.
        again = await _invoke([*prepare, *held])
        assert again.exit_code == 0 and "already prepared" in again.output
        assert _job(target, ids["running"])[1] == 4 and _comment(target) == prepared_marker

        # 5. A draft with one unresolved window is rejected whole: no plan, no change; check fails.
        evidence = tmp_path / "run-evidence.txt"
        evidence.write_text("provider ledger: request completed")
        full: list[dict[str, Any]] = []
        for entity, item in entities.items():
            kind = item["kind"]
            if kind == "notification" and entity.endswith(str(PENDING)):
                full.append(_decision(entity, "authorize_replay", accepts_duplicate=True))
            elif kind == "experiment_run":
                full.append(
                    _decision(
                        entity,
                        "confirmed_completed",
                        evidence={
                            "ref": evidence.name,
                            "sha256": recovery.digest(evidence.read_bytes()),
                        },
                    )
                )
            elif kind in {"experiment", "evaluator", "alert_rule", "project"}:
                full.append(_decision(entity, "keep_disabled"))
            elif kind == "audit_stream":
                full.append(_decision(entity, "suppress_restored_backlog_and_keep_disabled"))
            else:
                full.append(_decision(entity, "suppress"))
        binding = {
            "format": recovery.DECISIONS_FORMAT,
            "recovery_id": recovery_id,
            "manifest_sha256": manifest_sha,
        }
        draft = _write(
            tmp_path / "draft.json",
            {
                **binding,
                "decisions": [
                    {**d, "disposition": "unresolved"} if d["entity"].startswith("window:") else d
                    for d in full
                ],
            },
        )
        incomplete = _write(tmp_path / "incomplete.json", {**binding, "decisions": full[1:]})
        apply = ["restore-reconcile", "apply", "--manifest", str(manifest), *held]
        check = [
            "restore-reconcile",
            "check",
            "--manifest",
            str(manifest),
            "--app-role",
            APP_ROLE,
            *held,
        ]
        rejected = await _invoke([*apply, "--decisions", str(draft)])
        assert rejected.exit_code == 1 and "still unresolved" in rejected.output
        rejected = await _invoke([*apply, "--decisions", str(incomplete)])
        assert rejected.exit_code == 1 and "have no decision" in rejected.output
        assert not recovery.plan_path(manifest).exists()
        assert _comment(target) == prepared_marker and _job(target, ids["running"])[1] == 4
        assert _outbox(target, PENDING)[:3] == ("failed", 1, unresolved)
        early = await _invoke([*check, "--decisions", str(draft)])
        assert early.exit_code == 1 and "unresolved" in early.output

        # 6. The complete, immutable decision file applies once; check passes; reruns are no-ops.
        decisions = _write(tmp_path / "decisions.json", {**binding, "decisions": full})
        started = time.monotonic()
        applied = await _invoke([*apply, "--decisions", str(decisions)])
        timings["apply_s"] = time.monotonic() - started
        assert applied.exit_code == 0, applied.output
        plan = json.loads(recovery.plan_path(manifest).read_bytes())
        applied_marker = (
            f"{recovery.MARKER_PREFIX} id={recovery_id} manifest={manifest_sha} phase=applied "
            f"decisions={recovery.digest(decisions.read_bytes())} "
            f"plan={recovery.digest(recovery.plan_path(manifest).read_bytes())}"
        )
        assert _comment(target) == applied_marker
        assert _job(target, ids["queued"])[:4] == (
            "done",
            1,
            f"recovery:{recovery_id}:suppressed",
            None,
        )
        assert _job(target, ids["running"])[:4] == (
            "done",
            4,
            f"recovery:{recovery_id}:suppressed",
            None,
        )
        replay = _outbox(target, PENDING)
        assert replay[:4] == ("pending", 1, None, 0) and replay[4] == release_at
        assert _outbox(target, FAILED)[:3] == ("failed", 3, f"recovery:{recovery_id}:suppressed")
        assert _rows(target, "SELECT error FROM experiment_runs") == [
            (f"recovery:{recovery_id}:external_completed_result_unavailable",)
        ]
        assert _rows(target, "SELECT enabled, delivered_seq, last_error FROM audit_streams") == [
            (False, 3, f"recovery:{recovery_id}:suppressed_backlog_through_3")
        ]
        assert plan["final"]["window:openai-org-a"] is None

        started = time.monotonic()
        checked = await _invoke([*check, "--decisions", str(decisions)])
        timings["check_s"] = time.monotonic() - started
        assert checked.exit_code == 0, checked.output
        assert (
            f"recovery {recovery_id}" in checked.output
            and "authorized notification replays: 1" in checked.output
        )

        repeat = await _invoke([*apply, "--decisions", str(decisions)])
        assert repeat.exit_code == 0 and "already applied" in repeat.output
        assert _comment(target) == applied_marker and _job(target, ids["running"])[1] == 4
        assert (await _invoke([*check, "--decisions", str(decisions)])).exit_code == 0

        # Different decisions after apply: refused, nothing published or changed. A decision file
        # whose bytes changed no longer matches the published plan.
        other = _write(
            tmp_path / "other.json",
            {**binding, "decisions": [{**d, "reason": "changed"} for d in full]},
        )
        conflict = await _invoke([*apply, "--decisions", str(other)])
        assert conflict.exit_code == 1 and "different decisions" in conflict.output
        assert _comment(target) == applied_marker
        tampered = await _invoke([*check, "--decisions", str(other)])
        assert tampered.exit_code == 1 and "published apply plan differs" in tampered.output
        # A manifest whose bytes changed is not the one the marker binds, even with decisions
        # rebound to it; a missing manifest blocks every command.
        original = manifest.read_bytes()
        manifest.write_bytes(original.replace(b'"operator":"ops"', b'"operator":"eve"'))
        rebound = _write(
            tmp_path / "rebound.json",
            {
                **binding,
                "manifest_sha256": recovery.digest(manifest.read_bytes()),
                "decisions": full,
            },
        )
        tampered_manifest = await _invoke([*apply, "--decisions", str(rebound)])
        assert tampered_manifest.exit_code == 1, tampered_manifest.output
        assert "not prepared for this manifest" in tampered_manifest.output
        assert _comment(target) == applied_marker
        manifest.unlink()
        assert (await _invoke([*check, "--decisions", str(decisions)])).exit_code == 1
        manifest.write_bytes(original)
        assert (await _invoke([*check, "--decisions", str(decisions)])).exit_code == 0
        monkeypatch.setenv("EVALS_ENABLED", "true")
        get_settings.cache_clear()
        flags = await _invoke([*check, "--decisions", str(decisions)])
        assert flags.exit_code == 1 and "EVALS_ENABLED" in flags.output
        monkeypatch.setenv("EVALS_ENABLED", "false")
        get_settings.cache_clear()

        # 7. One lock per target, whatever the URL spelling: a held lock makes every command busy
        # without publishing; a competing prepare after the winner's commit conflicts.
        competing = tmp_path / "competing.json"
        with psycopg.connect(_libpq(target)) as holder:
            holder.execute("SELECT pg_advisory_xact_lock(%s)", (recovery.LOCK_KEY,))
            busy = await _invoke([*check, "--decisions", str(decisions)])
            assert busy.exit_code == 3 and "Busy" in busy.output
            monkeypatch.setenv("RESTORE_TARGET_URL", target.replace("localhost", "127.0.0.1"))
            assert (await _invoke([*check, "--decisions", str(decisions)])).exit_code == 3
            monkeypatch.setenv("RESTORE_TARGET_URL", target)
            busy = await _invoke([*prepare[:3], str(competing), *prepare[4:], *held])
            assert busy.exit_code == 3 and not competing.exists()
            holder.rollback()
        loser = await _invoke([*prepare[:3], str(competing), *prepare[4:], *held])
        assert loser.exit_code == 1 and "already carries recovery marker" in loser.output
        assert not competing.exists() and _comment(target) == applied_marker

        # 8. Resume against the restored copy with the release settings: the scheduler creates no
        # producer job, nothing quarantined is claimable, only the authorized replay is due, and
        # the ordinary retry route refuses the suppressed notification.
        release = Settings(
            _env_file=None, database_url=target_app, **{k.lower(): False for k in RELEASE_ENV}
        )
        engine = create_engine(target_app, pool_size=1)
        try:
            factory = create_session_factory(engine)
            async with factory() as db:
                created = await schedule_periodic(db, release, datetime.now(UTC))
                assert created and not set(created) & PRODUCER_KINDS
                claimed: set[int] = set()
                while (job := await claim_next(db)) is not None:
                    claimed.add(job.id)
                assert claimed and not claimed & set(ids.values())
                assert await schedule_periodic(db, release, datetime.now(UTC)) == []
                row = await claim_notification(db, release_at + timedelta(seconds=1))
                assert row is not None and row.id == PENDING
                assert await claim_notification(db, release_at + timedelta(seconds=1)) is None
                assert await retry_failed(db, ORG_A, CHANNEL_A, FAILED, datetime.now(UTC)) is False
                await db.rollback()
        finally:
            await engine.dispose()
    finally:
        get_settings.cache_clear()

    timings["restore_to_check_s"] = sum(
        timings[k] for k in ("restore_s", "prepare_s", "apply_s", "check_s")
    )
    print(
        "\nDRILL "
        + json.dumps(
            {
                "key": key,
                "schema_revision": document["schema_revision"],
                "dump_bytes": dump_bytes,
                "source_database_bytes": source_bytes,
                "backup_sha256": receipt_document["sha256"],
                "entities": len(entities),
                **{name: round(value, 2) for name, value in timings.items()},
            }
        )
    )
