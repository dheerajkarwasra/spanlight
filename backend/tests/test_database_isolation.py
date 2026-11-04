"""Harness contracts, independent of the application database fixtures."""

import os
import selectors
import subprocess
import sys
import time
from uuid import uuid4

import psycopg
import pytest
from hypothesis import given
from hypothesis import strategies as st

from tests.conftest import _base_database_url, _libpq, admin_database_url
from tests.database_lifecycle import drop_owned_database, owned_database


@pytest.fixture(autouse=True)
def clean_database() -> None:
    """Harness checks must run before a database can be trusted."""


def _database_url(**environment: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "from tests.conftest import admin_database_url; print(admin_database_url())",
        ],
        env={**os.environ, **environment},
        capture_output=True,
        text=True,
        check=False,
    )


def test_rejects_non_disposable_database_before_connecting() -> None:
    result = _database_url(
        TEST_DATABASE_URL="postgresql+psycopg://postgres:secret@localhost/spanlight"
    )
    assert result.returncode != 0
    assert "disposable" in result.stderr


def test_independent_invocations_get_distinct_databases() -> None:
    environment = {
        "TEST_DATABASE_URL": "postgresql+psycopg://postgres:secret@localhost/spanlight_test"
    }
    first = _database_url(**environment)
    second = _database_url(**environment)
    assert first.returncode == second.returncode == 0
    assert first.stdout.splitlines()[-1] != second.stdout.splitlines()[-1]


def _wait_for_ready(process: subprocess.Popen[str], timeout: float = 120) -> None:
    """Read available bytes so a partial line cannot defeat the readiness deadline."""
    assert process.stdout is not None
    output = b""
    deadline = time.monotonic() + timeout
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        while time.monotonic() < deadline:
            if not selector.select(max(0, deadline - time.monotonic())):
                break
            chunk = os.read(process.stdout.fileno(), 4096)
            if not chunk:
                break
            output += chunk
            if b"READY" in output.splitlines():
                return
    process.kill()
    _, errors = process.communicate(timeout=10)
    raise TimeoutError(f"Database child did not become ready: {errors}")


@pytest.mark.parametrize("failure_mode", ["normal", "kill_first", "timeout_second"])
def test_independent_runs_preserve_sentinel_rows(  # noqa: PLR0912 - three lifecycle failure scenarios.
    database_server: None,
    failure_mode: str,
) -> None:
    program = """
import sys
import time
import psycopg
import tests.conftest as fixtures
fixtures._RUN_ID = sys.argv[1]
from tests.conftest import app_database_url, admin_database_url, _libpq
owned = app_database_url.__wrapped__(None)
next(owned)
with psycopg.connect(_libpq(admin_database_url()), autocommit=True) as db:
    db.execute('CREATE TABLE harness_sentinel (value text)')
    db.execute("INSERT INTO harness_sentinel VALUES ('still here')")
    print('READY', flush=True)
    if sys.argv[2] == 'stall':
        time.sleep(30)
    sys.stdin.readline()
    assert db.execute('SELECT value FROM harness_sentinel').fetchone() == ('still here',)
try:
    next(owned)
except StopIteration:
    pass
"""
    base_url = _libpq(_base_database_url())
    identities = [uuid4().hex, uuid4().hex]
    worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
    databases = [f"spanlight_test_{identity}_{worker}" for identity in identities]
    environment = {**os.environ, "TEST_DATABASE_URL": _base_database_url()}
    first = subprocess.Popen(  # noqa: S603 - fixed Python program, no user input.
        [sys.executable, "-c", program, identities[0], "normal"],
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    second: subprocess.Popen[str] | None = None
    try:
        _wait_for_ready(first)
        mode = "stall" if failure_mode == "timeout_second" else "normal"
        second = subprocess.Popen(  # noqa: S603 - fixed Python program, no user input.
            [sys.executable, "-c", program, identities[1], mode],
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        _wait_for_ready(second)
        if failure_mode == "timeout_second":
            with pytest.raises(subprocess.TimeoutExpired):
                second.communicate("continue\n", timeout=0.1)
        else:
            _, errors = second.communicate("continue\n", timeout=120)
            assert second.returncode == 0, errors
            if failure_mode == "kill_first":
                first.kill()
                first.communicate(timeout=10)
            else:
                _, errors = first.communicate("continue\n", timeout=120)
                assert first.returncode == 0, errors
    finally:
        for process in (first, second):
            if process is not None and process.poll() is None:
                process.kill()
                process.communicate(timeout=10)
        # A killed child cannot run its generator finally; the parent owns exact identities.
        failure = sys.exception()
        cleanup_failure: Exception | None = None
        for database in databases:
            try:
                drop_owned_database(base_url, database)
            except Exception as cleanup_error:  # noqa: BLE001 - try both owned identities before reporting.
                if failure is not None:
                    failure.add_note(f"Owned database {database} cleanup failed: {cleanup_error}")
                elif cleanup_failure is None:
                    cleanup_failure = cleanup_error
                else:
                    cleanup_failure.add_note(str(cleanup_error))
        if cleanup_failure is not None:
            raise cleanup_failure

    with psycopg.connect(base_url, connect_timeout=10) as connection:
        remaining = connection.execute(
            "SELECT datname FROM pg_database WHERE datname = ANY(%s)", (databases,)
        ).fetchall()
        assert remaining == []


@given(st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789", min_size=1, max_size=30))
def test_rejects_arbitrary_database_names(name: str) -> None:
    with pytest.MonkeyPatch.context() as environment:
        environment.setenv("TEST_DATABASE_URL", f"postgresql://postgres@localhost/{name}")
        with pytest.raises(ValueError, match="disposable"):
            admin_database_url()


def test_readiness_timeout_reports_child_stderr() -> None:
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys, time; "
            "print('startup stalled', file=sys.stderr, flush=True); time.sleep(30)",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        with pytest.raises(TimeoutError, match="startup stalled"):
            _wait_for_ready(process, timeout=0.1)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()


def test_ambiguous_create_cleans_exact_owned_database_and_preserves_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    statements: list[str] = []
    failure = ConnectionError("CREATE acknowledgement lost")

    class FaultyConnection:
        def __enter__(self) -> "FaultyConnection":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def execute(self, statement: object) -> None:
            query = statement if isinstance(statement, str) else statement.as_string()  # type: ignore[attr-defined] # psycopg composable boundary
            if query.startswith("SET "):
                return
            statements.append(query)
            if query.startswith("CREATE DATABASE"):
                raise failure
            raise ConnectionError("cleanup transport unavailable")

    # Transport fault injection is necessary to reproduce a lost success acknowledgement.
    def connect(
        dsn: str,
        *,
        autocommit: bool = False,
        connect_timeout: int = 0,
    ) -> FaultyConnection:
        assert dsn == "postgresql://postgres@localhost/spanlight_test"
        assert autocommit, "CREATE/DROP must execute outside a transaction"
        assert connect_timeout > 0, "Remote connection waits must be bounded"
        return FaultyConnection()

    monkeypatch.setattr("tests.database_lifecycle.psycopg.connect", connect)
    database = "spanlight_test_0123456789abcdef0123456789abcdef_main"
    with (
        pytest.raises(ConnectionError, match="CREATE acknowledgement lost") as caught,
        owned_database("postgresql://postgres@localhost/spanlight_test", database),
    ):
        pytest.fail("CREATE failure must prevent schema setup")
    assert caught.value is failure
    assert statements == [
        f'CREATE DATABASE "{database}"',
        f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)',
    ]
    assert "cleanup transport unavailable" in caught.value.__notes__[0]


@pytest.mark.parametrize("database", ["spanlight", "spanlight_test", "spanlight_test_other_main"])
def test_cleanup_rejects_unowned_database_before_connecting(database: str) -> None:
    with pytest.raises(ValueError, match="ownership identity"):
        drop_owned_database("invalid://must-not-connect", database)
