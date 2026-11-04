"""Real IAM enforcement and backup streams against invocation-owned MinIO and PostgreSQL."""

import asyncio
import base64
import json
import os
import shutil
import subprocess
import time
from collections.abc import AsyncIterator, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import boto3
import httpx
import psycopg
import pytest
from botocore.config import Config
from botocore.exceptions import ClientError
from testcontainers.core.container import DockerContainer
from testcontainers.core.network import Network
from typer.testing import CliRunner

from app import cli
from app.backups.jobs import run_backup_database
from app.backups.retention import backup_key
from app.backups.service import BackupError, run_backup
from app.backups.storage import get_backup_object_store
from app.config import Settings, get_settings
from app.jobs.context import TaskContext
from app.jobs.outcome import JobOutcome
from app.jobs.queue import claim_next, enqueue
from app.storage.object_store import get_object_store
from app.storage.s3 import PART_SIZE
from tests.conftest import admin_database_url

MINIO_IMAGE = "pgsty/minio:RELEASE.2026-08-04T00-00-00Z"
MC_IMAGE = "pgsty/mc:RELEASE.2026-09-16T00-00-00Z"
ROOT_USER = "disposable-root"
ROOT_PASSWORD = "disposable-root-secret"
BACKUPS = "backups-test"
EXPORTS = "exports-test"


def _client(endpoint: str, user: str, secret: str) -> Any:
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=user,
        aws_secret_access_key=secret,
        region_name="us-east-1",
        config=Config(
            s3={"addressing_style": "path"},
            retries={"max_attempts": 0},
            request_checksum_calculation="when_required",
        ),
    )


@pytest.fixture(scope="module")
def minio() -> Iterator[tuple[DockerContainer, str, Any]]:
    with (
        Network() as network,
        (
            DockerContainer(MINIO_IMAGE)
            .with_network(network)
            .with_network_aliases("minio")
            .with_env("MINIO_ROOT_USER", ROOT_USER)
            .with_env("MINIO_ROOT_PASSWORD", ROOT_PASSWORD)
            .with_exposed_ports(9000)
            .with_command("server /data")
        ) as container,
    ):
        endpoint = f"http://{container.get_container_host_ip()}:{container.get_exposed_port(9000)}"
        admin = _client(endpoint, ROOT_USER, ROOT_PASSWORD)
        for attempt in range(100):
            try:
                admin.list_buckets()
                break
            except Exception:
                if attempt == 99:
                    raise
                time.sleep(0.1)
        docker = shutil.which("docker")
        assert docker is not None
        root = Path(__file__).resolve().parents[3]
        rendered = subprocess.run(  # noqa: S603 - fixed repo-owned Compose inputs
            [
                docker,
                "compose",
                "-f",
                str(root / "deploy/compose.yaml"),
                "-f",
                str(root / "deploy/compose.minio.yaml"),
                "--env-file",
                os.devnull,
                "config",
                "--format",
                "json",
            ],
            env={
                "HOME": os.environ["HOME"],
                "PATH": os.environ["PATH"],
                "APP_DB_PASSWORD": "disposable-password",
                "SECRET_KEY": "disposable-secret",
                "POSTGRES_PASSWORD": "disposable-password",
            },
            capture_output=True,
            text=True,
            check=True,
        )
        initialization = json.loads(rendered.stdout)["services"]["minio-init"]
        environment = initialization["environment"]
        environment.update(
            {
                "MINIO_ROOT_USER": ROOT_USER,
                "MINIO_ROOT_PASSWORD": ROOT_PASSWORD,
                "S3_BUCKET": EXPORTS,
                "BACKUP_S3_BUCKET": BACKUPS,
            }
        )
        for kind, principal in (
            ("EXPORT", "export"),
            ("UPLOAD", "upload"),
            ("RECOVERY", "recovery"),
            ("RETENTION", "retention"),
        ):
            environment[f"MINIO_{kind}_USER"] = principal
            environment[f"MINIO_{kind}_PASSWORD"] = f"{principal}-secret-password"
        # Execute the shipped init image, script and policies on this invocation's network.
        script = initialization["entrypoint"][-1].replace("$$", "$")
        initializer = DockerContainer(MC_IMAGE).with_network(network).with_kwargs(entrypoint=["sh"])
        for key, value in environment.items():
            initializer.with_env(key, value)
        initializer.with_command(["-c", script])
        with initializer:
            result = initializer.get_wrapped_container().wait(timeout=30)
            assert result["StatusCode"] == 0, initializer.get_logs()
        # Disposable admin alias supports deliberate per-object fault injection below.
        _mc(container, ["alias", "set", "local", "http://127.0.0.1:9000", ROOT_USER, ROOT_PASSWORD])
        for bucket in (BACKUPS, EXPORTS):
            admin.put_bucket_versioning(
                Bucket=bucket, VersioningConfiguration={"Status": "Enabled"}
            )
        yield container, endpoint, admin


def _mc(container: DockerContainer, arguments: list[str]) -> None:
    result = container.exec(["mc", *arguments])
    assert result.exit_code == 0, result.output


@pytest.fixture(scope="module")
def pg17_client() -> Iterator[DockerContainer]:
    """Client tools match the owned PostgreSQL 17 server, without modifying the host.

    `host.docker.internal` reaches the host's published database port: Docker Desktop resolves it
    already, and on Linux engines the `host-gateway` alias maps it to the bridge gateway.
    """
    client = DockerContainer("postgres:17").with_command("sleep infinity")
    client.with_kwargs(extra_hosts={"host.docker.internal": "host-gateway"})
    with client as container:
        yield container


@pytest.fixture
def pg17_tools(
    pg17_client: DockerContainer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    docker = shutil.which("docker")
    assert docker is not None
    binary = tmp_path / "pg_dump"
    container_id = pg17_client.get_wrapped_container().id
    binary.write_text(
        "#!/usr/bin/env python3\n"
        "import os, sys\n"
        f"args = [{docker!r}, 'exec', '-i']\n"
        "for name in ('PGPORT', 'PGUSER', 'PGPASSWORD', 'PGDATABASE', 'PGSSLMODE'):\n"
        "    if name in os.environ: args += ['-e', name]\n"
        "args += ['-e', 'PGHOST=host.docker.internal']\n"
        f"args += [{container_id!r}, 'pg_dump', *sys.argv[1:]]\n"
        "os.execv(args[0], args)\n"
    )
    binary.chmod(0o700)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    return binary


def _settings(endpoint: str, principal: str, **extra: Any) -> Settings:
    return Settings(
        _env_file=None,
        backup_s3_endpoint=endpoint,
        backup_s3_bucket=BACKUPS,
        backup_s3_access_key=principal,
        backup_s3_secret_key=f"{principal}-secret-password",
        backup_s3_force_path_style=True,
        backup_s3_sse="",  # this MinIO has no KMS, so it refuses SSE-S3 (as in compose.minio.yaml)
        **extra,
    )


def _denied(client: Any, operation: str, **arguments: Any) -> None:
    with pytest.raises(ClientError) as error:
        getattr(client, operation)(**arguments)
    assert error.value.response["Error"]["Code"] == "AccessDenied"


@pytest.mark.parametrize("principal", ["export", "upload", "recovery", "retention"])
def test_direct_principal_permissions(
    minio: tuple[DockerContainer, str, Any], principal: str
) -> None:
    _, endpoint, admin = minio
    key = f"backups/{principal}.dump"
    version = admin.put_object(Bucket=BACKUPS, Key=key, Body=b"backup")["VersionId"]
    client = _client(endpoint, principal, f"{principal}-secret-password")
    operations = {
        "list_objects_v2": {"Bucket": BACKUPS, "Prefix": "backups/"},
        "get_object": {"Bucket": BACKUPS, "Key": key},
        "put_object": {"Bucket": BACKUPS, "Key": key, "Body": b"upload"},
        "delete_object": {"Bucket": BACKUPS, "Key": key},
    }
    allowed = {
        "export": set(),
        "upload": {"put_object"},
        "recovery": {"list_objects_v2", "get_object"},
        "retention": {"list_objects_v2", "delete_object"},
    }[principal]
    for operation, arguments in operations.items():
        if operation in allowed:
            response = getattr(client, operation)(**arguments)
            if operation == "get_object":
                assert response["Body"].read() == b"backup"
                response["Body"].close()
        else:
            _denied(client, operation, **arguments)
    if principal == "recovery":
        # This pinned MinIO aliases GetObjectVersion to GetObject; denying versions also
        # denies current reads. Hosted rollout must verify its provider's separate actions.
        response = client.get_object(Bucket=BACKUPS, Key=key, VersionId=version)
        assert response["Body"].read() == b"backup"
        response["Body"].close()
    else:
        _denied(client, "get_object", Bucket=BACKUPS, Key=key, VersionId=version)
    _denied(client, "delete_object", Bucket=BACKUPS, Key=key, VersionId=version)
    if principal in {"recovery", "retention"}:
        assert client.list_object_versions(Bucket=BACKUPS, Prefix="backups/")["Versions"]
    else:
        _denied(client, "list_object_versions", Bucket=BACKUPS, Prefix="backups/")
    if principal != "export":
        _denied(client, "put_object", Bucket=EXPORTS, Key="exports/test", Body=b"forbidden")
        _denied(client, "list_objects_v2", Bucket=BACKUPS, Prefix="unrelated/")


async def test_factory_isolation_and_export_crud(minio: tuple[DockerContainer, str, Any]) -> None:
    _, endpoint, _ = minio
    settings = _settings(
        endpoint,
        "upload",
        s3_endpoint=endpoint,
        s3_bucket=EXPORTS,
        s3_access_key="export",
        s3_secret_key="export-secret-password",
        s3_force_path_style=True,
    )
    exports = get_object_store(settings)
    backups = get_backup_object_store(settings)
    assert exports is not None and backups is not None
    assert get_object_store(settings) is exports
    assert get_backup_object_store(settings) is backups
    assert exports is not backups
    assert (
        get_backup_object_store(
            Settings(
                _env_file=None,
                s3_bucket=EXPORTS,
                s3_access_key="export",
                s3_secret_key="export-secret-password",
            )
        )
        is None
    )
    await exports.put("exports/test", b"export data", "text/plain")
    assert b"".join([chunk async for chunk in exports.open("exports/test")]) == b"export data"
    async with httpx.AsyncClient() as client:
        response = await client.get(exports.presigned_get_url("exports/test", 60))
    assert response.status_code == 200 and response.content == b"export data"
    assert [info.key for info in await exports.list("exports/")] == ["exports/test"]
    await exports.delete("exports/test")
    assert await exports.list("exports/") == []


@pytest.mark.parametrize("size", [20, PART_SIZE + 20])
async def test_restricted_upload_small_and_multipart(
    minio: tuple[DockerContainer, str, Any], size: int
) -> None:
    _, endpoint, admin = minio
    store = get_backup_object_store(_settings(endpoint, "upload"))
    assert store is not None
    key = f"backups/size-{size}.dump"

    async def stream() -> AsyncIterator[bytes]:
        yield b"a" * size

    await store.put(key, stream(), "application/octet-stream")
    response = admin.get_object(Bucket=BACKUPS, Key=key)
    assert response["Body"].read() == b"a" * size
    response["Body"].close()


@pytest.mark.parametrize("cancel", [False, True])
async def test_multipart_failure_and_cancellation_abort(
    minio: tuple[DockerContainer, str, Any], cancel: bool
) -> None:
    _, endpoint, admin = minio
    store = get_backup_object_store(_settings(endpoint, "upload"))
    assert store is not None
    key = f"backups/interrupted-{cancel}.dump"
    uploaded = asyncio.Event()

    async def stream() -> AsyncIterator[bytes]:
        yield b"a" * PART_SIZE
        uploaded.set()
        if cancel:
            await asyncio.Event().wait()
        raise RuntimeError("source failed after first part")

    task = asyncio.create_task(store.put(key, stream(), "application/octet-stream"))
    await asyncio.wait_for(uploaded.wait(), timeout=20)
    if cancel:
        task.cancel()
    with pytest.raises(asyncio.CancelledError if cancel else RuntimeError):
        await task
    assert not admin.list_objects_v2(Bucket=BACKUPS, Prefix=key).get("Contents")
    assert not admin.list_multipart_uploads(Bucket=BACKUPS, Prefix=key).get("Uploads")


@contextmanager
def _payload(size: int) -> Iterator[None]:
    with psycopg.connect(
        admin_database_url().replace("postgresql+psycopg://", "postgresql://")
    ) as db:
        db.execute("CREATE TABLE task8a_dump_payload (body bytea)")
        if size:
            db.execute("INSERT INTO task8a_dump_payload VALUES (%s)", (os.urandom(size),))
    try:
        yield
    finally:
        with psycopg.connect(
            admin_database_url().replace("postgresql+psycopg://", "postgresql://")
        ) as db:
            db.execute("DROP TABLE task8a_dump_payload")


@pytest.mark.parametrize("size", [0, PART_SIZE + 1024 * 1024])
async def test_real_postgres_dump(
    minio: tuple[DockerContainer, str, Any], app_database_url: str, pg17_tools: Path, size: int
) -> None:
    _, endpoint, admin = minio
    settings = _settings(endpoint, "upload", backup_database_url=admin_database_url())
    store = get_backup_object_store(settings)
    assert store is not None
    now = datetime(2025, 1, 2, tzinfo=UTC)
    with _payload(size):
        result = await run_backup(settings, store, now)
    assert (result.size_bytes > PART_SIZE) == bool(size)
    response = admin.get_object(Bucket=BACKUPS, Key=result.key)
    payload = response["Body"].read()
    response["Body"].close()
    assert payload.startswith(b"PGDMP") and len(payload) == result.size_bytes


@pytest.mark.parametrize("size", [0, PART_SIZE + 1024 * 1024])
async def test_failed_pg_dump_never_commits_or_needs_delete(
    minio: tuple[DockerContainer, str, Any],
    app_database_url: str,
    tmp_path: Path,
    pg17_tools: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    size: int,
) -> None:
    _, endpoint, admin = minio
    binary = str(pg17_tools)
    fault_dir = tmp_path / "fault"
    fault_dir.mkdir()
    wrapper = fault_dir / "pg_dump"
    wrapper.write_text(f'#!/bin/sh\n"{binary}" "$@"\nexit 23\n')
    wrapper.chmod(0o700)
    monkeypatch.setenv("PATH", f"{fault_dir}{os.pathsep}{os.environ['PATH']}")
    settings = _settings(endpoint, "upload", backup_database_url=admin_database_url())
    store = get_backup_object_store(settings)
    assert store is not None
    now = datetime(2025, 1, 3, tzinfo=UTC)
    with _payload(size), pytest.raises(BackupError, match="exited with status 23"):
        await run_backup(settings, store, now)
    assert not admin.list_multipart_uploads(Bucket=BACKUPS, Prefix=backup_key(now)).get("Uploads")
    assert not admin.list_objects_v2(Bucket=BACKUPS, Prefix=backup_key(now)).get("Contents")
    assert "backup.discard_failed" not in caplog.text


async def test_cancelled_real_dump_aborts_multipart_and_stops_child(
    minio: tuple[DockerContainer, str, Any],
    app_database_url: str,
    pg17_tools: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _, endpoint, admin = minio
    fault_dir = tmp_path / "cancel"
    fault_dir.mkdir()
    wrapper = fault_dir / "pg_dump"
    marker = tmp_path / "dump-finished"
    wrapper.write_text(
        "#!/usr/bin/env python3\nimport subprocess, sys, time\n"
        f"subprocess.run([{str(pg17_tools)!r}, *sys.argv[1:]], check=True)\n"
        f"open({str(marker)!r}, 'w').close()\n"
        "while True: time.sleep(1)\n"
    )
    wrapper.chmod(0o700)
    monkeypatch.setenv("PATH", f"{fault_dir}{os.pathsep}{os.environ['PATH']}")
    settings = _settings(endpoint, "upload", backup_database_url=admin_database_url())
    store = get_backup_object_store(settings)
    assert store is not None
    now = datetime(2025, 1, 4, tzinfo=UTC)
    with _payload(PART_SIZE + 1024 * 1024):
        task = asyncio.create_task(run_backup(settings, store, now))
        try:
            async with asyncio.timeout(30):
                while not marker.exists() or not admin.list_multipart_uploads(  # noqa: ASYNC110
                    Bucket=BACKUPS, Prefix=backup_key(now)
                ).get("Uploads"):
                    await asyncio.sleep(0.05)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            task.cancel()
            if not task.done():
                with pytest.raises(asyncio.CancelledError):
                    await task
    assert not admin.list_objects_v2(Bucket=BACKUPS, Prefix=backup_key(now)).get("Contents")
    assert not admin.list_multipart_uploads(Bucket=BACKUPS, Prefix=backup_key(now)).get("Uploads")
    assert "backup.discard_failed" not in caplog.text
    assert "subprocess.stop_timed_out" not in caplog.text


def test_prune_preview_apply_and_failure(
    minio: tuple[DockerContainer, str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, endpoint, admin = minio
    now = datetime.now(UTC)
    old = backup_key(now - timedelta(days=300))
    newest = backup_key(now)
    unknown = "backups/unknown.keep"
    for key in (old, newest, unknown):
        admin.put_object(Bucket=BACKUPS, Key=key, Body=b"keep or prune")
    for name, value in {
        "BACKUP_S3_ENDPOINT": endpoint,
        "BACKUP_S3_BUCKET": BACKUPS,
        "BACKUP_S3_ACCESS_KEY": "retention",
        "BACKUP_S3_SECRET_KEY": "retention-secret-password",
        "BACKUP_S3_FORCE_PATH_STYLE": "true",
        "BACKUPS_ENABLED": "false",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("BACKUP_DATABASE_URL", raising=False)
    get_settings.cache_clear()
    try:
        runner = CliRunner()
        preview = runner.invoke(cli.app, ["backup", "prune"])
        assert preview.exit_code == 0, preview.output
        assert old in preview.output and "deleted 0" in preview.output
        assert admin.get_object(Bucket=BACKUPS, Key=old)["Body"].read() == b"keep or prune"
        concurrent = backup_key(now + timedelta(days=1))
        admin.put_object(Bucket=BACKUPS, Key=concurrent, Body=b"new upload")
        fresh_stale = backup_key(now - timedelta(days=301))
        admin.put_object(Bucket=BACKUPS, Key=fresh_stale, Body=b"newly discovered stale object")
        applied = runner.invoke(cli.app, ["backup", "prune", "--apply"])
        assert applied.exit_code == 0, applied.output
        keys = {
            item["Key"]
            for item in admin.list_objects_v2(Bucket=BACKUPS, Prefix="backups/")["Contents"]
        }
        assert old not in keys and fresh_stale not in keys
        assert fresh_stale in applied.output
        assert {newest, unknown, concurrent} <= keys
        # Recovery principal can list but cannot delete: command must report failure.
        admin.put_object(Bucket=BACKUPS, Key=old, Body=b"old")
        monkeypatch.setenv("BACKUP_S3_ACCESS_KEY", "recovery")
        monkeypatch.setenv("BACKUP_S3_SECRET_KEY", "recovery-secret-password")
        get_settings.cache_clear()
        denied = runner.invoke(cli.app, ["backup", "prune", "--apply"])
        assert denied.exit_code == 1 and "failed" in denied.output
        monkeypatch.setenv("BACKUP_S3_ACCESS_KEY", "upload")
        monkeypatch.setenv("BACKUP_S3_SECRET_KEY", "upload-secret-password")
        get_settings.cache_clear()
        listing_denied = runner.invoke(cli.app, ["backup", "prune"])
        assert listing_denied.exit_code == 1 and "backup listing failed" in listing_denied.output
    finally:
        get_settings.cache_clear()


def test_prune_partial_failure_continues_other_deletions(
    minio: tuple[DockerContainer, str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container, endpoint, admin = minio
    now = datetime.now(UTC)
    refused = backup_key(now - timedelta(days=500))
    allowed = backup_key(now - timedelta(days=501))
    newest = backup_key(now)
    for key in (refused, allowed, newest):
        admin.put_object(Bucket=BACKUPS, Key=key, Body=b"backup")
    # Add one explicit object denial to the real retention principal.
    policy = json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Deny",
                    "Action": ["s3:DeleteObject"],
                    "Resource": [f"arn:aws:s3:::{BACKUPS}/{refused}"],
                }
            ],
        }
    )
    encoded = base64.b64encode(policy.encode()).decode()
    result = container.exec(["sh", "-c", f"echo {encoded} | base64 -d > /tmp/refused.json"])
    assert result.exit_code == 0
    _mc(container, ["admin", "policy", "create", "local", "refuse-one", "/tmp/refused.json"])  # noqa: S108 - owned container policy fixture
    _mc(container, ["admin", "policy", "attach", "local", "refuse-one", "--user", "retention"])
    for key, value in {
        "BACKUP_S3_ENDPOINT": endpoint,
        "BACKUP_S3_BUCKET": BACKUPS,
        "BACKUP_S3_ACCESS_KEY": "retention",
        "BACKUP_S3_SECRET_KEY": "retention-secret-password",
        "BACKUP_S3_FORCE_PATH_STYLE": "true",
    }.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    try:
        applied = CliRunner().invoke(cli.app, ["backup", "prune", "--apply"])
        assert applied.exit_code == 1, applied.output
        assert "failed 1" in applied.output
        keys = {
            item["Key"]
            for item in admin.list_objects_v2(Bucket=BACKUPS, Prefix="backups/")["Contents"]
        }
        assert refused in keys and allowed not in keys and newest in keys
    finally:
        get_settings.cache_clear()
        _mc(container, ["admin", "policy", "detach", "local", "refuse-one", "--user", "retention"])


async def test_routine_backup_job_uploads_without_retention_permissions(
    minio: tuple[DockerContainer, str, Any],
    app_database_url: str,
    pg17_tools: Path,
    session_factory: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _, endpoint, admin = minio
    settings = _settings(endpoint, "upload", backup_database_url=admin_database_url())
    async with session_factory() as db:
        await enqueue(db, "backup_database")
        job = await claim_next(db)
        await db.commit()
    assert job is not None
    result = await run_backup_database(TaskContext(job, settings, session_factory), {})
    assert result == JobOutcome.OK
    assert admin.list_objects_v2(Bucket=BACKUPS, Prefix="backups/")["Contents"]
    assert "backup_prune_failed" not in caplog.text
    assert "backup_prune_delete_failed" not in caplog.text


def test_backup_cli_uses_separate_upload_and_recovery_principals(
    minio: tuple[DockerContainer, str, Any],
    app_database_url: str,
    pg17_tools: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, endpoint, _ = minio
    for name, value in {
        "BACKUP_S3_ENDPOINT": endpoint,
        "BACKUP_S3_BUCKET": BACKUPS,
        "BACKUP_S3_ACCESS_KEY": "upload",
        "BACKUP_S3_SECRET_KEY": "upload-secret-password",
        "BACKUP_S3_FORCE_PATH_STYLE": "true",
        "BACKUP_S3_SSE": "",
        "BACKUPS_ENABLED": "false",
        "BACKUP_DATABASE_URL": admin_database_url(),
    }.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    try:
        runner = CliRunner()
        made = runner.invoke(cli.app, ["backup", "now"])
        assert made.exit_code == 0 and "Backed up to backups/" in made.output
        denied = runner.invoke(cli.app, ["backup", "list"])
        assert denied.exit_code == 1
        monkeypatch.setenv("BACKUP_S3_ACCESS_KEY", "recovery")
        monkeypatch.setenv("BACKUP_S3_SECRET_KEY", "recovery-secret-password")
        get_settings.cache_clear()
        listed = runner.invoke(cli.app, ["backup", "list"])
        assert listed.exit_code == 0 and "spanlight-" in listed.output
    finally:
        get_settings.cache_clear()


def test_distinct_key_upload_during_prune_survives(
    minio: tuple[DockerContainer, str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, endpoint, admin = minio
    now = datetime.now(UTC)
    oldest = now - timedelta(days=700)
    keys = [backup_key(oldest + timedelta(seconds=index)) for index in range(100)]
    for key in keys:
        admin.put_object(Bucket=BACKUPS, Key=key, Body=b"stale")
    admin.put_object(Bucket=BACKUPS, Key=backup_key(now), Body=b"current")
    for name, value in {
        "BACKUP_S3_ENDPOINT": endpoint,
        "BACKUP_S3_BUCKET": BACKUPS,
        "BACKUP_S3_ACCESS_KEY": "retention",
        "BACKUP_S3_SECRET_KEY": "retention-secret-password",
        "BACKUP_S3_FORCE_PATH_STYLE": "true",
    }.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(CliRunner().invoke, cli.app, ["backup", "prune", "--apply"])
            deadline = time.monotonic() + 20
            while admin.list_objects_v2(Bucket=BACKUPS, Prefix=keys[0]).get("Contents"):
                assert time.monotonic() < deadline
                assert not future.done()
                time.sleep(0.01)
            assert not future.done(), "Upload must overlap actual pruning"
            uploaded = backup_key(now + timedelta(seconds=1))
            admin.put_object(Bucket=BACKUPS, Key=uploaded, Body=b"concurrent upload")
            result = future.result(timeout=20)
        assert result.exit_code == 0, result.output
        response = admin.get_object(Bucket=BACKUPS, Key=uploaded)
        assert response["Body"].read() == b"concurrent upload"
        response["Body"].close()
    finally:
        get_settings.cache_clear()
