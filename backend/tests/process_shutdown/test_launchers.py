"""Actual production entrypoints preserve proxy/port/security and readiness ordering."""

import asyncio
import contextlib
import json
import signal
import socket
import sys
from collections.abc import AsyncIterator

import httpx
import pytest
from alembic.script import ScriptDirectory
from sqlalchemy import text

from app.config import Settings
from app.db.migrations import alembic_config
from tests.jobs.registry_support import SessionFactory
from tests.process_shutdown.support import Child

pytestmark = pytest.mark.serial

HEAD = ScriptDirectory.from_config(alembic_config()).get_current_head()


@contextlib.asynccontextmanager
async def launch(
    module: str, settings: Settings, port: int, *, host: str | None = None
) -> AsyncIterator[Child]:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "tests.process_shutdown.launcher_child",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    assert process.stdin is not None
    process.stdin.write(
        json.dumps(
            {
                "module": module,
                "host": host,
                "environment": {
                    "DATABASE_URL": settings.database_url
                    + f"?application_name=task15d_launcher_{process.pid}",
                    "MIGRATION_DATABASE_URL": "test-only-must-be-removed",
                    "PORT": str(port),
                    "GATEWAY_HOST": "127.0.0.1",
                    "LOG_JSON": "true",
                    "WORKER_REQUIRED": "true",
                    "DEMO_ENABLED": "false",
                    "PROVIDER_STATUS_POLLING_ENABLED": "false",
                    "BACKUPS_ENABLED": "false",
                    "SHUTDOWN_TIMEOUT_SECONDS": "2",
                    "SHUTDOWN_WORK_SECONDS": "0.2",
                    "SHUTDOWN_FLUSH_SECONDS": "0.3",
                    "SHUTDOWN_CLOSE_SECONDS": "0.3",
                },
            }
        ).encode()
        + b"\n"
    )
    await process.stdin.drain()
    process.stdin.close()
    run = Child(process)
    try:
        yield run
    except BaseException:
        print("".join(run.lines))
        raise
    finally:
        if process.returncode is None:
            process.kill()
        await run.finish()


@pytest.mark.parametrize("module", ["app.server", "app.gateway"])
async def test_production_http_launch_preserves_port_proxy_and_drops_owner_url(
    module: str,
    settings: Settings,
) -> None:
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    async with launch(module, settings, port) as run:
        base = await run.ready()
        assert (await run.event("launcher_owner"))["owns_executor"]
        async with httpx.AsyncClient(trust_env=False) as http:
            response = await http.get(
                base + "/launcher-probe", headers={"X-Forwarded-For": "192.0.2.7"}
            )
        assert response.json() == {
            "client": "192.0.2.7",
            "migration_present": False,
            "port": port,
            "proxy_headers": True,
        }
        run.process.send_signal(signal.SIGTERM)
        await run.finish()
        assert run.process.returncode == -signal.SIGTERM


async def test_schema_app_role_then_api_liveness_worker_and_readiness(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    # The fixture owns a UUID database, migrates it, then exposes an unprivileged application role.
    async with session_factory() as db:
        assert await db.scalar(text("SELECT version_num FROM alembic_version")) == HEAD
        assert await db.scalar(
            text(
                "SELECT NOT rolsuper AND NOT rolbypassrls FROM pg_roles WHERE rolname=current_user"
            )
        )
        await db.execute(text("DELETE FROM worker_heartbeats"))
        await db.commit()
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    async with launch("app.server", settings, port) as api:
        base = await api.ready()
        async with httpx.AsyncClient(trust_env=False) as http:
            before = await http.get(base + "/health/ready")
            assert before.status_code == 503
            assert before.json()["migrations"] == "ok"
            async with launch("app.jobs.worker", settings, 1) as worker:
                await worker.event("worker_started")
                async with asyncio.timeout(8):
                    # Poll the real child HTTP endpoint, not an in-process event.
                    while (await http.get(base + "/health/ready")).status_code != 200:  # noqa: ASYNC110
                        await asyncio.sleep(0.02)
                worker.process.send_signal(signal.SIGTERM)
                await worker.finish()
                assert worker.process.returncode == -signal.SIGTERM
        api.process.send_signal(signal.SIGTERM)
        await api.finish()
        assert api.process.returncode == -signal.SIGTERM


async def test_local_maintenance_drains_writer_processes_before_schema_verification(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    # External traffic control is represented by admitting no new work during this gate.
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    async with (
        launch("app.server", settings, port) as api,
        launch("app.jobs.worker", settings, 1) as worker,
    ):
        await api.ready()
        await worker.event("worker_started")
        api.process.send_signal(signal.SIGTERM)
        await api.finish()
        worker.process.send_signal(signal.SIGTERM)
        await worker.finish()
        assert api.process.returncode == worker.process.returncode == -signal.SIGTERM
        async with session_factory() as db:
            assert (
                await db.scalar(
                    text(
                        "SELECT count(*) FROM pg_stat_activity "
                        "WHERE application_name IN (:api, :worker)"
                    ),
                    {
                        "api": f"task15d_launcher_{api.process.pid}",
                        "worker": f"task15d_launcher_{worker.process.pid}",
                    },
                )
                == 0
            )
            assert await db.scalar(text("SELECT count(*) FROM jobs WHERE status='running'")) == 0
            assert await db.scalar(text("SELECT version_num FROM alembic_version")) == HEAD


@pytest.mark.parametrize("module", ["app.server", "app.gateway"])
async def test_bind_failure_closes_started_lifespan(
    module: str,
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    with socket.socket() as reserved:
        reserved.bind(("0.0.0.0" if module == "app.server" else "127.0.0.1", 0))  # noqa: S104 - match launcher bind
        reserved.listen()
        async with launch(module, settings, reserved.getsockname()[1]) as run:
            await run.finish()
            assert run.process.returncode == 3
            async with session_factory() as db:
                assert (
                    await db.scalar(
                        text("SELECT count(*) FROM pg_stat_activity WHERE application_name=:name"),
                        {"name": f"task15d_launcher_{run.process.pid}"},
                    )
                    == 0
                )


async def test_shared_launcher_preserves_explicit_ipv6_host_override(settings: Settings) -> None:
    with socket.socket(socket.AF_INET6) as reserved:
        reserved.bind(("::1", 0))
        port = reserved.getsockname()[1]
    async with launch("app.server", settings, port, host="::1") as run:
        async with asyncio.timeout(8), httpx.AsyncClient(trust_env=False) as http:
            while True:
                try:
                    response = await http.get(f"http://[::1]:{port}/health/live")
                    if response.status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                assert not run.reader.done(), "".join(run.lines)
                await asyncio.sleep(0.01)
        assert (await run.event("launcher_owner"))["owns_executor"]
        run.process.send_signal(signal.SIGTERM)
        await run.finish()
        assert run.process.returncode == -signal.SIGTERM


@pytest.mark.parametrize("module", ["app.server", "app.gateway", "app.jobs.worker"])
async def test_first_sigint_joins_and_preserves_signal_exit(
    module: str, settings: Settings
) -> None:
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    async with launch(module, settings, port) as run:
        if module == "app.jobs.worker":
            await run.event("worker_started")
            assert (await run.event("worker_owner"))["owns_executor"]
        else:
            await run.ready()
            assert (await run.event("launcher_owner"))["owns_executor"]
        run.process.send_signal(signal.SIGINT)
        await run.finish()
        assert run.process.returncode == -signal.SIGINT
