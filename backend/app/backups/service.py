"""Run `pg_dump` into the object store, and `pg_restore` back out of it.

The database URL carries a password, so it never reaches a command line (visible in `ps` and in
process-accounting logs) or a log line: it is parsed here and handed to the child process as
libpq environment variables (`PGHOST`, `PGPASSWORD`, ...). Error messages name the offending
parameter, never its value.
"""

import asyncio
import contextlib
import os
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from datetime import datetime

import structlog
from botocore.exceptions import ClientError
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from app.backups.retention import backup_key
from app.config import Settings
from app.storage.object_store import ObjectStore

logger = structlog.get_logger(__name__)

PG_DUMP = "pg_dump"
PG_RESTORE = "pg_restore"
PSQL = "psql"
CONTENT_TYPE = "application/octet-stream"
# How much of the tool's stderr is kept for the error message.
STDERR_TAIL_BYTES = 2048
CHUNK_SIZE = 1024 * 1024
# How long stopping a child may take after it was killed.
STOP_TIMEOUT_SECONDS = 5.0
# How long the emptiness check of a restore target may take.
CHECK_TIMEOUT_SECONDS = 30.0

# URL query parameters (and the URL parts) that map to a libpq setting, and its variable.
_LIBPQ_ENVIRONMENT = {
    "host": "PGHOST",
    "port": "PGPORT",
    "user": "PGUSER",
    "password": "PGPASSWORD",
    "dbname": "PGDATABASE",
    "sslmode": "PGSSLMODE",
    "sslrootcert": "PGSSLROOTCERT",
    "sslcert": "PGSSLCERT",
    "sslkey": "PGSSLKEY",
    "connect_timeout": "PGCONNECT_TIMEOUT",
    "application_name": "PGAPPNAME",
}
# The child gets these from our environment and nothing else: no S3 keys, no SECRET_KEY, and no
# stray PG* variable that would override the URL.
_INHERITED_ENVIRONMENT = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "SSL_CERT_FILE")


class BackupError(Exception):
    """A backup could not be made. The message is safe to log and to store on the job."""


class BackupNotConfiguredError(BackupError):
    """`BACKUP_DATABASE_URL` is not set."""


class RestoreError(Exception):
    """A restore could not be done. The message is safe to print."""


@dataclass(frozen=True, slots=True)
class BackupResult:
    key: str
    size_bytes: int


def connection_params(url: str) -> dict[str, str]:
    """Split a `postgresql[+driver]://` URL into libpq settings.

    Raises ValueError with a message that never quotes `url`.
    """
    try:
        parsed = make_url(url)
    except (ArgumentError, ValueError):
        # A malformed port raises a ValueError that quotes it, and without a host the password
        # lands there.
        raise ValueError("the database URL is not a valid URL") from None
    if not parsed.drivername.startswith("postgres"):
        raise ValueError("the database URL must start with postgresql://")

    params = {
        "host": parsed.host,
        "port": None if parsed.port is None else str(parsed.port),
        "user": parsed.username,
        "password": parsed.password,
        "dbname": parsed.database,
    }
    for name, value in parsed.query.items():
        if name not in _LIBPQ_ENVIRONMENT or not isinstance(value, str):
            raise ValueError(f"the database URL has an unsupported parameter {name!r}")
        params[name] = value
    return {name: value for name, value in params.items() if value is not None}


def _process_environment(params: Mapping[str, str]) -> dict[str, str]:
    environment = {name: os.environ[name] for name in _INHERITED_ENVIRONMENT if name in os.environ}
    environment.update({_LIBPQ_ENVIRONMENT[name]: value for name, value in params.items()})
    return environment


class _Process:
    """A child process whose stderr is drained as it runs, keeping the last bytes of it.

    Draining matters: a child that fills the stderr pipe while nobody reads it blocks forever.
    """

    def __init__(self, process: asyncio.subprocess.Process, secret: str | None) -> None:
        self.process = process
        self._secret = secret
        self._tail = bytearray()
        self._drain = asyncio.create_task(self._drain_stderr())

    async def _drain_stderr(self) -> None:
        assert self.process.stderr is not None  # noqa: S101 - spawned with stderr=PIPE
        while chunk := await self.process.stderr.read(4096):
            self._tail += chunk
            del self._tail[:-STDERR_TAIL_BYTES]

    async def finish(self) -> tuple[int, str]:
        """Wait for exit; the status and the stderr tail, with the password masked."""
        code = await self.process.wait()
        await self._drain
        tail = self._tail.decode("utf-8", errors="replace").strip()
        if self._secret:
            tail = tail.replace(self._secret, "***")
        return code, tail

    async def stop(self) -> None:
        """Kill the child if it is still running and reap it. Safe to call twice.

        Since Python 3.12 `Process.wait()` returns only once the child's pipes are closed, and a
        pipe nobody reads never reaches end of file: a killed `pg_dump` whose output is still
        unread would make `wait()` hang. So the output is read and thrown away until it ends, and
        the whole step has a deadline, so stopping can never block the caller (or a cancellation).
        """
        process = self.process
        if process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
        try:
            async with asyncio.timeout(STOP_TIMEOUT_SECONDS):
                await asyncio.gather(_discard_output(process.stdout), process.wait())
        except TimeoutError:
            logger.warning("subprocess.stop_timed_out", pid=process.pid)
        except Exception:
            logger.warning("subprocess.stop_failed", pid=process.pid, exc_info=True)
        self._drain.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._drain


async def _discard_output(stream: asyncio.StreamReader | None) -> None:
    if stream is None:
        return
    while await stream.read(CHUNK_SIZE):
        pass


class _DumpStream:
    """`pg_dump`'s standard output as an async iterator that also checks how it exited.

    Raising at end of output, before the store sees the end of its input, is what keeps a failed
    dump out of the bucket: the store aborts the upload instead of completing it.
    """

    def __init__(self, process: _Process) -> None:
        self._process = process
        self.size = 0
        self.complete = False

    async def chunks(self) -> AsyncIterator[bytes]:
        stdout = self._process.process.stdout
        assert stdout is not None  # noqa: S101 - spawned with stdout=PIPE
        while chunk := await stdout.read(CHUNK_SIZE):
            self.size += len(chunk)
            yield chunk
        code, tail = await self._process.finish()
        if code != 0:
            raise BackupError(f"{PG_DUMP} exited with status {code}: {tail}")
        self.complete = True


async def run_backup(settings: Settings, store: ObjectStore, now: datetime) -> BackupResult:
    """Dump the database to `backups/YYYY/MM/DD/spanlight-<UTC timestamp>.dump`.

    The dump is streamed from `pg_dump` to the store, so its size is not bound by memory or
    disk. On any failure the child is killed and nothing is left under the key.
    """
    if settings.backup_database_url is None:
        raise BackupNotConfiguredError("BACKUP_DATABASE_URL is not set")
    try:
        params = connection_params(settings.backup_database_url.get_secret_value())
    except ValueError as error:
        raise BackupError(f"BACKUP_DATABASE_URL is invalid: {error}") from None

    key = backup_key(now)
    try:
        spawned = await asyncio.create_subprocess_exec(
            PG_DUMP,
            "-Fc",
            "--no-unlogged-table-data",
            "--no-password",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_process_environment(params),
        )
    except FileNotFoundError:
        raise BackupError(f"{PG_DUMP} is not installed or not on PATH") from None

    process = _Process(spawned, params.get("password"))
    stream = _DumpStream(process)
    try:
        await store.put(key, stream.chunks(), CONTENT_TYPE)
        if not stream.complete:
            raise BackupError("the upload ended before pg_dump finished")
    except BaseException:
        await process.stop()
        await _discard(store, key)
        raise
    return BackupResult(key=key, size_bytes=stream.size)


async def _discard(store: ObjectStore, key: str) -> None:
    """Best effort: remove whatever a failed backup may have left under `key`."""
    try:
        await store.delete(key)
    except Exception:
        logger.warning("backup.discard_failed", key=key, exc_info=True)


async def restore_backup(store: ObjectStore, key: str, target_url: str) -> None:
    """Restore the dump under `key` into an empty database, all or nothing.

    Refuses a target that has any table in `public`. The dump is streamed into `pg_restore`
    without touching disk, and runs as one transaction, so a failure leaves the target empty.

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
    environment = _process_environment(params)
    conninfo = f"dbname={_conninfo_value(params['dbname'])}"

    await _require_empty_database(conninfo, environment, params.get("password"))

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

    process = _Process(spawned, params.get("password"))
    try:
        await _feed(process, store, key)
        code, tail = await process.finish()
    except BaseException:
        await process.stop()
        raise
    if code != 0:
        raise RestoreError(f"{PG_RESTORE} exited with status {code}: {tail}")


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


async def _feed(process: _Process, store: ObjectStore, key: str) -> None:
    stdin = process.process.stdin
    assert stdin is not None  # noqa: S101 - spawned with stdin=PIPE
    try:
        async with _closing(store.open(key)) as chunks:
            async for chunk in chunks:
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


_COUNT_TABLES = """
    SELECT count(*)
    FROM pg_class AS c JOIN pg_namespace AS n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
"""


async def _require_empty_database(
    conninfo: str, environment: Mapping[str, str], secret: str | None
) -> None:
    """Fail unless the target has no table in `public`. Asks through `psql`, in a child process."""
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
            _COUNT_TABLES,
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
            f"the target database already has {tables} table(s) in the public schema; "
            "restore into a new, empty database"
        )
