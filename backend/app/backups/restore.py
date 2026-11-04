"""Restore a dump from the object store into an empty database with `pg_restore`."""

import asyncio
import contextlib
import hashlib
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from botocore.exceptions import ClientError

from app.backups.child import STDERR_TAIL_BYTES, ChildProcess
from app.backups.libpq import connection_params, process_environment
from app.storage.object_store import ObjectStore

PG_RESTORE = "pg_restore"
PSQL = "psql"
# How long the emptiness check of a restore target may take.
CHECK_TIMEOUT_SECONDS = 30.0


class RestoreError(Exception):
    """A restore could not be done. The message is safe to print."""


@dataclass(frozen=True, slots=True)
class RestoreReceipt:
    """What was restored: the SHA-256 and size of the exact bytes `pg_restore` consumed, the
    target (no password) and the time bounds. Only a successful restore produces one."""

    key: str
    sha256: str
    bytes: int
    target: dict[str, str]
    started_at: datetime
    finished_at: datetime


async def restore_backup(store: ObjectStore, key: str, target_url: str) -> RestoreReceipt:
    """Restore the dump under `key` into an empty database, all or nothing.

    Refuses a target that has any relation outside the system schemas. The dump is streamed into
    `pg_restore` without touching disk, and runs as one transaction, so a failure leaves the
    target empty.

    The target URL must name the host and the database. The emptiness check and `pg_restore` run
    as child processes with the same explicit settings and the same minimal environment, so the
    database that is checked is the database that is restored into, whatever `PG*` variables the
    operator's shell has set.
    """
    try:
        params = connection_params(target_url)
    except ValueError as error:
        raise RestoreError(f"the target URL is invalid: {error}") from None
    for required in ("host", "dbname"):
        if required not in params:
            raise RestoreError(f"the target URL must name the {required}")
    environment = process_environment(params)
    conninfo = f"dbname={_conninfo_value(params['dbname'])}"

    await _require_empty_database(conninfo, environment, params.get("password"))

    started_at = datetime.now(UTC)
    try:
        spawned = await asyncio.create_subprocess_exec(
            PG_RESTORE,
            "--no-owner",
            "--no-privileges",
            "--no-password",
            "--single-transaction",
            "-d",
            conninfo,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            env=environment,
        )
    except FileNotFoundError:
        raise RestoreError(f"{PG_RESTORE} is not installed or not on PATH") from None

    process = ChildProcess(spawned, params.get("password"))
    try:
        sha256, size = await _feed(process, store, key)
        code, tail = await process.finish()
    except BaseException:
        await process.stop()
        raise
    if code != 0:
        raise RestoreError(f"{PG_RESTORE} exited with status {code}: {tail}")
    target: dict[str, str] = {
        name: params[name] for name in ("host", "port", "dbname") if name in params
    }
    return RestoreReceipt(key, sha256, size, target, started_at, datetime.now(UTC))


def _conninfo_value(value: str) -> str:
    """Quote `value` as a libpq connection-string value, so `=`, spaces and quotes stay literal."""
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


@contextlib.asynccontextmanager
async def _closing[IteratorT: AsyncIterator[bytes]](
    iterator: IteratorT,
) -> AsyncIterator[IteratorT]:
    """Close `iterator` on exit when it is an async generator, so a download is not left open."""
    try:
        yield iterator
    finally:
        aclose = getattr(iterator, "aclose", None)
        if aclose is not None:
            await aclose()


async def _feed(process: ChildProcess, store: ObjectStore, key: str) -> tuple[str, int]:
    """Stream the object into `pg_restore`; the SHA-256 and count of the bytes it was fed."""
    stdin = process.process.stdin
    assert stdin is not None  # noqa: S101 - spawned with stdin=PIPE
    digest = hashlib.sha256()
    size = 0
    try:
        async with _closing(store.open(key)) as chunks:
            async for chunk in chunks:
                digest.update(chunk)
                size += len(chunk)
                stdin.write(chunk)
                await stdin.drain()
    except (BrokenPipeError, ConnectionResetError):
        # pg_restore stopped reading; its exit status and stderr say why.
        pass
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") in {"NoSuchKey", "404"}:
            raise RestoreError(f"no backup with key {key!r}") from None
        raise RestoreError(f"could not read {key!r} from the object store") from error
    finally:
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            stdin.close()
            await stdin.wait_closed()
    return digest.hexdigest(), size


# Tables, views, sequences and foreign tables in any schema an application could have created.
_COUNT_RELATIONS = """
    SELECT count(*)
    FROM pg_class AS c JOIN pg_namespace AS n ON n.oid = c.relnamespace
    WHERE n.nspname <> 'information_schema' AND n.nspname NOT LIKE 'pg\\_%'
      AND c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f')
"""


async def _require_empty_database(
    conninfo: str, environment: Mapping[str, str], secret: str | None
) -> None:
    """Fail unless the target has no relation outside the system schemas. Asks through `psql`, in
    a child process. An empty target is necessary, not proof that it was created for this
    restore: the runbook asks for a newly created database and retained creation evidence."""
    try:
        child = await asyncio.create_subprocess_exec(
            PSQL,
            "-X",
            "-q",
            "-A",
            "-t",
            "--no-password",
            "-v",
            "ON_ERROR_STOP=1",
            "-d",
            conninfo,
            "-c",
            _COUNT_RELATIONS,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=dict(environment),
        )
    except FileNotFoundError:
        raise RestoreError(f"{PSQL} is not installed or not on PATH") from None
    try:
        async with asyncio.timeout(CHECK_TIMEOUT_SECONDS):
            stdout, stderr = await child.communicate()
    except TimeoutError:
        child.kill()
        await child.communicate()
        raise RestoreError("timed out connecting to the target database") from None
    except BaseException:
        child.kill()
        await child.communicate()
        raise
    if child.returncode != 0:
        detail = stderr.decode("utf-8", errors="replace").strip()[-STDERR_TAIL_BYTES:]
        if secret:
            detail = detail.replace(secret, "***")
        raise RestoreError(f"cannot check the target database: {detail}")
    try:
        tables = int(stdout.decode().strip())
    except ValueError:
        raise RestoreError("cannot check the target database: unexpected answer") from None
    if tables:
        raise RestoreError(
            f"the target database already has {tables} relation(s) outside the system schemas; "
            "restore into a new, empty database"
        )
