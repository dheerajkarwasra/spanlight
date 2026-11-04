"""Reconcile a restored database before it is reopened: quarantine, decide, check.

`spanlight restore-reconcile prepare|apply|check` run against the restored database only (the
operator passes `RESTORE_TARGET_URL`; there is no fallback to `DATABASE_URL`). They share one
fixed transaction advisory lock, so two operators cannot publish competing plans; they bind the
target by server endpoint, database name and OID and by a bounded `COMMENT ON DATABASE`; and
they keep their plans in immutable, fsynced JSON files next to the operator. No table, migration,
service or endpoint is added. Everything here is cooperative bookkeeping for the operator, not a
security boundary against the database owner, and it never starts a service: the external
"all services held" boundary stays the operator's job until check passes.

Phases, as the database COMMENT records them:

- `prepare`: every queued or running job, every queued or running experiment or evaluator run,
  every pending or failed notification, every experiment schedule, evaluator, alert rule, project
  digest setting and audit stream is inventoried with its preimage, then put into a terminal
  quarantine image (failed, lease cleared, fence bumped once, `recovery:<id>:unresolved`). The
  manifest (preimages, postimages, payload hashes, verification results) is published before the
  database commit; a rerun with the same manifest is idempotent and never bumps a fence twice.
- `apply`: a complete, unique decision per entity and per external window; any `unresolved` or
  missing decision rejects the whole invocation before a plan is published or a row changes. The
  apply plan is published, then the quarantine images are re-checked under row locks and the
  final images and COMMENT written in one transaction. An identical rerun is a no-op.
- `check`: read-only; passes only in the `applied` phase with matching digests, final images,
  no new queue work, every producer disabled and the release settings off.
"""

import hashlib
import json
import os
import re
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import psycopg
from psycopg import sql
from pydantic import BaseModel, ConfigDict, Field

from app.backups.libpq import connection_params
from app.config import Settings
from app.core.crypto import CryptoError, Sealed, decrypt
from app.notifications.outbox import RECOVERY_ERROR_PREFIX
from app.services.audit import _CHECK_CHAIN

MANIFEST_FORMAT = "spanlight-recovery-manifest/1"
CONTEXT_FORMAT = "spanlight-recovery-context/1"
DECISIONS_FORMAT = "spanlight-recovery-decisions/1"
PLAN_FORMAT = "spanlight-recovery-plan/1"
RECEIPT_FORMAT = "spanlight-restore-receipt/1"
# The marker written as the database COMMENT; prefix, then `key=value` pairs.
MARKER_PREFIX = "spanlight-recovery/1"
# Prefix of every `last_error` / `error` written here; `retry_failed` refuses rows carrying it.
ERROR_PREFIX = RECOVERY_ERROR_PREFIX
# One reserved key for every recovery command on a target. Advisory locks are per database, so
# the lock is scoped to the connected target whatever the URL spelling.
LOCK_KEY = 8_112_026_000_000_001
MAX_FILE_BYTES = 16 * 1024 * 1024
_MARKER = re.compile(
    rf"^{re.escape(MARKER_PREFIX)} id=(?P<id>[0-9a-f-]{{36}}) "
    r"manifest=(?P<manifest>[0-9a-f]{64}) "
    r"phase=(?P<phase>prepared|applied)"
    r"(?: decisions=(?P<decisions>[0-9a-f]{64}) plan=(?P<plan>[0-9a-f]{64}))?$"
)


class RecoveryError(Exception):
    """A recovery command could not be done. The message is safe to print."""


class RecoveryBusyError(RecoveryError):
    """Another recovery command holds the target's lock; nothing was published or changed."""


# ---------------------------------------------------------------- files and canonical form


def _plain(value: Any) -> Any:
    """The JSON form of a database value: UTC ISO timestamps, decimals and UUIDs as strings,
    bytes as their SHA-256, so an image compares and serializes the same way every time."""
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, bytes | memoryview):
        return hashlib.sha256(bytes(value)).hexdigest()
    if isinstance(value, dict):
        return hashlib.sha256(canonical_json(value)).hexdigest()
    return value


def canonical_json(document: Any) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), default=_plain).encode()


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_json(path: Path) -> tuple[dict[str, Any], str]:
    """A bounded JSON object file and its SHA-256. Symlinks and oversized files are refused."""
    if path.is_symlink():
        raise RecoveryError(f"{path.name} is a symlink; recovery files must be regular files")
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            raise RecoveryError(f"{path.name} is larger than {MAX_FILE_BYTES} bytes")
        data = path.read_bytes()
    except OSError as error:
        raise RecoveryError(f"cannot read {path.name}: {error.strerror}") from None
    try:
        document = json.loads(data)
    except ValueError:
        raise RecoveryError(f"{path.name} is not valid JSON") from None
    if not isinstance(document, dict):
        raise RecoveryError(f"{path.name} must hold a JSON object")
    return document, digest(data)


def write_immutable(path: Path, data: bytes) -> None:
    """Publish `data` at `path` durably without ever replacing different content.

    The bytes go to a mode-0600 temporary file that is fsynced, hard-linked into place (which
    fails if the name exists) and the directory is fsynced. An existing identical file is fine;
    an existing different file or a symlink is an error.
    """
    if path.is_symlink():
        raise RecoveryError(f"{path.name} is a symlink; refusing to write through it")
    if path.exists():
        if path.read_bytes() == data:
            return
        raise RecoveryError(f"{path.name} exists with different content; refusing to replace it")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != data:
                raise RecoveryError(
                    f"{path.name} appeared with different content; refusing to replace it"
                ) from None
    except OSError as error:
        raise RecoveryError(f"cannot write {path.name}: {error.strerror}") from None
    finally:
        temporary.unlink(missing_ok=True)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


# ---------------------------------------------------------------- operator input contracts


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Window(_Strict):
    """An external account, destination, producer or upstream retry queue whose effects between
    the backup and the isolation are not in the restored database and need a decision."""

    id: str = Field(pattern=r"^[A-Za-z0-9._-]{1,100}$")
    kind: Literal["provider_account", "destination", "producer", "upstream_retry"]
    description: str = Field(min_length=1, max_length=500)
    from_at: datetime
    to_at: datetime


class Context(_Strict):
    """What only the operator knows. Every field is required: a missing fact stays unresolved."""

    format: Literal["spanlight-recovery-context/1"]
    operator: str = Field(min_length=1, max_length=200)
    # Provenance of the freshly created target, and its name at restore time if renamed since.
    target_created_at: datetime
    target_creation_evidence: str = Field(min_length=1, max_length=500)
    renamed_from: str | None = Field(default=None, max_length=200)
    # Conservative bounds of the dump's snapshot, when writers stopped, when reopening is planned.
    backup_started_at: datetime
    backup_finished_at: datetime
    isolation_at: datetime
    release_at: datetime
    services_held: list[str] = Field(min_length=1, max_length=100)
    upstream_retries_held: Literal[True]
    windows: list[Window] = Field(max_length=1000)
    verification_evidence: list[str] = Field(max_length=100)


class _ReceiptTarget(_Strict):
    host: str = Field(min_length=1, max_length=253)
    port: str | None = Field(default=None, max_length=5)
    dbname: str = Field(min_length=1, max_length=63)


class _ReceiptDestination(_Strict):
    bucket: str | None = Field(default=None, max_length=255)
    endpoint: str | None = Field(default=None, max_length=512)


class Receipt(_Strict):
    """What `spanlight restore --receipt` writes; `prepare` refuses anything else."""

    format: Literal["spanlight-restore-receipt/1"]
    key: str = Field(min_length=1, max_length=1024)
    destination: _ReceiptDestination
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    bytes: int = Field(ge=0, strict=True)
    target: _ReceiptTarget
    started_at: datetime
    finished_at: datetime
    operator: str = Field(min_length=1, max_length=200)


class Evidence(_Strict):
    # A file next to the decision file; no directories, so evidence stays where the runbook says.
    ref: str = Field(pattern=r"^[^/\\]{1,255}$")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    # For a notification confirmed delivered: when the receiver got it, from its own record.
    sent_at: datetime | None = None


Disposition = Literal[
    "confirmed_completed",
    "suppress",
    "authorize_replay",
    "keep_disabled",
    "suppress_restored_backlog_and_keep_disabled",
    "unresolved",
]


class Decision(_Strict):
    entity: str = Field(min_length=1, max_length=200)
    disposition: Disposition
    operator: str = Field(min_length=1, max_length=200)
    decided_at: datetime
    reason: str = Field(min_length=1, max_length=2000)
    evidence: Evidence | None = None
    # `authorize_replay` must say so: a replay may charge or deliver twice.
    accepts_duplicate: bool = False


class Decisions(_Strict):
    format: Literal["spanlight-recovery-decisions/1"]
    recovery_id: uuid.UUID
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    decisions: list[Decision] = Field(max_length=100_000)


def _validate[ModelT: BaseModel](
    model: type[ModelT], document: Mapping[str, Any], name: str
) -> ModelT:
    try:
        return model.model_validate(document)
    except ValueError as error:
        first = str(error).splitlines()[1:3]
        raise RecoveryError(f"{name} is malformed: {' '.join(first).strip()}") from None


# ---------------------------------------------------------------- inventory and images


@dataclass(frozen=True)
class _Kind:
    table: str
    key: str
    # The columns an image records and compares; `hashed` ones are stored as SHA-256 only.
    columns: tuple[str, ...]
    hashed: tuple[str, ...]
    # Which rows are inventoried.
    where: str
    allowed: frozenset[str]


_EFFECT = frozenset({"confirmed_completed", "suppress", "authorize_replay"})
_KINDS: dict[str, _Kind] = {
    "job": _Kind(
        "jobs",
        "id",
        (
            "status",
            "lease_until",
            "fence",
            "last_error",
            "outcome",
            "attempts",
            "kind",
            "dedupe_key",
        ),
        ("payload",),
        "status IN ('queued', 'running')",
        _EFFECT,
    ),
    "experiment_run": _Kind(
        "experiment_runs",
        "id",
        ("status", "error", "finished_at", "heartbeat_at", "experiment_id"),
        (),
        "status IN ('queued', 'running')",
        _EFFECT,
    ),
    "evaluator_run": _Kind(
        "evaluator_runs",
        "id",
        (
            "status",
            "error",
            "finished_at",
            "heartbeat_at",
            "cost_usd",
            "reserved_usd",
            "evaluator_id",
        ),
        (),
        "status IN ('queued', 'running')",
        _EFFECT,
    ),
    "notification": _Kind(
        "notification_outbox",
        "id",
        (
            "status",
            "lease_until",
            "fence",
            "last_error",
            "attempts",
            "next_attempt_at",
            "sent_at",
            "kind",
        ),
        ("payload", "target"),
        "status IN ('pending', 'failed')",
        _EFFECT,
    ),
    "experiment": _Kind(
        "experiments", "id", ("schedule",), (), "TRUE", frozenset({"keep_disabled"})
    ),
    "evaluator": _Kind("evaluators", "id", ("enabled",), (), "TRUE", frozenset({"keep_disabled"})),
    "alert_rule": _Kind(
        "alert_rules", "id", ("enabled",), (), "TRUE", frozenset({"keep_disabled"})
    ),
    "project": _Kind(
        "projects", "id", ("weekly_digest_enabled",), (), "TRUE", frozenset({"keep_disabled"})
    ),
    "audit_stream": _Kind(
        "audit_streams",
        "org_id",
        (
            "enabled",
            "delivered_seq",
            "last_delivered_at",
            "last_error",
            "last_error_at",
            "consecutive_failures",
        ),
        ("secret_enc",),
        "TRUE",
        frozenset({"keep_disabled", "suppress_restored_backlog_and_keep_disabled"}),
    ),
}
# Entities with no database row: decided, recorded in the plan, never written to the database.
_EXTERNAL = {"window": _EFFECT, "experiment_week": _EFFECT}


def _marker(recovery_id: str, state: str) -> str:
    return f"{ERROR_PREFIX}{recovery_id}:{state}"


def _quarantine(kind: str, pre: dict[str, Any], recovery_id: str, at: str) -> dict[str, Any]:
    """The terminal image `prepare` writes for one row."""
    unresolved = _marker(recovery_id, "unresolved")
    match kind:
        case "job":
            return {
                **pre,
                "status": "failed",
                "lease_until": None,
                "fence": pre["fence"] + 1,
                "last_error": unresolved,
                "outcome": None,
            }
        case "experiment_run":
            return {
                **pre,
                "status": "failed",
                "error": unresolved,
                "finished_at": at,
                "heartbeat_at": None,
            }
        case "evaluator_run":
            # Conservative estimate, not verified billing: a reservation may have been spent.
            cost = max(Decimal(pre["cost_usd"]), Decimal(pre["reserved_usd"]))
            return {
                **pre,
                "status": "failed",
                "error": unresolved,
                "finished_at": at,
                "heartbeat_at": None,
                "cost_usd": _plain(cost),
                "reserved_usd": "0",
            }
        case "notification":
            return {
                **pre,
                "status": "failed",
                "lease_until": None,
                "fence": pre["fence"] + 1,
                "last_error": unresolved,
            }
        case "experiment":
            return {"schedule": None}
        case "evaluator" | "alert_rule":
            return {"enabled": False}
        case "project":
            return {"weekly_digest_enabled": False}
        case "audit_stream":
            return {**pre, "enabled": False, "last_error": unresolved, "last_error_at": at}
    raise RecoveryError(f"unknown entity kind {kind!r}")


def _final(
    kind: str,
    entity: dict[str, Any],
    decision: Decision,
    recovery_id: str,
    release_at: str,
) -> dict[str, Any] | None:
    """The image `apply` writes for one decided entity; None for an external-only entity."""
    if kind in _EXTERNAL:
        return None
    post = entity["post"]
    disposition = decision.disposition
    match kind:
        case "job":
            # `jobs_outcome_check` lists the allowed outcomes and Stage C adds no migration, so a
            # reconciled job is `done` with no outcome; its disposition is in `last_error`.
            return {
                **post,
                "status": "done",
                "last_error": _marker(recovery_id, _STATE[disposition]),
            }
        case "experiment_run" | "evaluator_run":
            # A paid call that completed is not a completed run: results are not available.
            state = (
                "external_completed_result_unavailable"
                if disposition == "confirmed_completed"
                else _STATE[disposition]
            )
            return {**post, "error": _marker(recovery_id, state)}
        case "notification":
            if disposition == "confirmed_completed":
                assert decision.evidence is not None  # noqa: S101 - validated in _plan
                return {
                    **post,
                    "status": "sent",
                    "sent_at": _plain(decision.evidence.sent_at),
                    "last_error": _marker(recovery_id, "confirmed_completed"),
                }
            if disposition == "authorize_replay":
                # Back to the queue through the ordinary lease path, after the release time.
                return {
                    **post,
                    "status": "pending",
                    "attempts": 0,
                    "next_attempt_at": release_at,
                    "last_error": None,
                }
            return {**post, "last_error": _marker(recovery_id, "suppressed")}
        case "experiment" | "evaluator" | "alert_rule" | "project":
            return dict(post)
        case "audit_stream":
            if disposition == "suppress_restored_backlog_and_keep_disabled":
                head = entity["restored_head_seq"]
                return {
                    **post,
                    "delivered_seq": head,
                    "last_error": _marker(recovery_id, f"suppressed_backlog_through_{head}"),
                }
            return {**post, "last_error": _marker(recovery_id, "keep_disabled")}
    return None


_STATE = {
    "confirmed_completed": "confirmed_completed",
    "suppress": "suppressed",
    "authorize_replay": "replay_authorized",
}


def _iso_weeks(start: datetime, end: datetime) -> list[str]:
    """Every ISO week from `start`'s through `end`'s, as `YYYY-Www`."""
    day = start.astimezone(UTC).date()
    last = end.astimezone(UTC).date()
    monday = day - timedelta(days=day.weekday())
    weeks = []
    while monday <= last:
        year, week, _ = monday.isocalendar()
        weeks.append(f"{year}-W{week:02d}")
        monday += timedelta(days=7)
    return weeks


# ---------------------------------------------------------------- the target database


@dataclass(frozen=True)
class _Identity:
    endpoint: str
    dbname: str
    oid: int
    comment: str | None
    schema_revision: str


def _connect(target_url: str) -> psycopg.Connection[tuple[Any, ...]]:
    try:
        params = connection_params(target_url)
    except ValueError as error:
        raise RecoveryError(f"RESTORE_TARGET_URL is invalid: {error}") from None
    for required in ("host", "dbname"):
        if required not in params:
            raise RecoveryError(f"RESTORE_TARGET_URL must name the {required}")
    conninfo = target_url.replace("postgresql+psycopg://", "postgresql://", 1)
    try:
        return psycopg.connect(conninfo, autocommit=False, application_name="spanlight-recovery")
    except psycopg.OperationalError:
        raise RecoveryError("cannot connect to RESTORE_TARGET_URL") from None


def _lock(connection: psycopg.Connection[tuple[Any, ...]]) -> None:
    row = connection.execute("SELECT pg_try_advisory_xact_lock(%s)", (LOCK_KEY,)).fetchone()
    if row is None or not row[0]:
        raise RecoveryBusyError(
            "another recovery command holds this target; retry when it has finished"
        )


def _identity(connection: psycopg.Connection[tuple[Any, ...]]) -> _Identity:
    """Fresh authoritative reads, after the lock. Requires the database owner (or a superuser)."""
    row = connection.execute(
        """
        SELECT d.oid, d.datname, shobj_description(d.oid, 'pg_database'),
               coalesce(host(inet_server_addr()) || ':' || inet_server_port(), 'local'),
               pg_has_role(current_user, d.datdba, 'USAGE')
                   OR (SELECT rolsuper FROM pg_roles WHERE rolname = current_user)
        FROM pg_database d WHERE d.datname = current_database()
        """
    ).fetchone()
    assert row is not None  # noqa: S101 - current_database() always exists
    oid, dbname, comment, endpoint, owner = row
    if not owner:
        raise RecoveryError("RESTORE_TARGET_URL must connect as the database owner")
    try:
        revisions = connection.execute("SELECT version_num FROM alembic_version").fetchall()
    except psycopg.errors.UndefinedTable:
        raise RecoveryError("the target has no alembic_version table; restore it first") from None
    if len(revisions) != 1:
        raise RecoveryError("the target's alembic_version must hold exactly one revision")
    return _Identity(endpoint, dbname, int(oid), comment, str(revisions[0][0]))


def _parse_marker(comment: str | None) -> dict[str, str] | None:
    if comment is None or not comment.startswith(MARKER_PREFIX):
        return None
    match = _MARKER.match(comment)
    if match is None:
        raise RecoveryError(
            "the target's COMMENT carries a recovery marker this version cannot read"
        )
    return {name: value for name, value in match.groupdict().items() if value is not None}


def _set_comment(connection: psycopg.Connection[tuple[Any, ...]], dbname: str, marker: str) -> None:
    connection.execute(
        sql.SQL("COMMENT ON DATABASE {} IS {}").format(sql.Identifier(dbname), sql.Literal(marker))
    )


def _read_images(
    connection: psycopg.Connection[tuple[Any, ...]],
    kind: _Kind,
    keys: Iterable[Any] | None,
    *,
    lock: bool,
) -> dict[str, dict[str, Any]]:
    """Images by key: the inventoried rows when `keys` is None, else exactly those rows."""
    columns = [sql.Identifier(kind.key), *map(sql.Identifier, kind.columns + kind.hashed)]
    query = sql.SQL("SELECT {} FROM {} WHERE ").format(
        sql.SQL(", ").join(columns), sql.Identifier(kind.table)
    )
    if keys is None:
        query += sql.SQL(kind.where)
        params: tuple[Any, ...] = ()
    else:
        query += sql.SQL("{} = ANY(%s)").format(sql.Identifier(kind.key))
        params = (list(keys),)
    query += sql.SQL(" ORDER BY {}").format(sql.Identifier(kind.key))
    if lock:
        query += sql.SQL(" FOR UPDATE")
    names = kind.columns + kind.hashed
    return {
        str(row[0]): {name: _plain(value) for name, value in zip(names, row[1:], strict=True)}
        for row in connection.execute(query, params)
    }


def _compare(
    connection: psycopg.Connection[tuple[Any, ...]],
    entities: Mapping[str, dict[str, Any]],
    image: str,
    *,
    lock: bool,
) -> None:
    """Fail unless every database entity's row matches its `image` (`pre`, `post` or `final`)."""
    for name, kind in _KINDS.items():
        expected = {
            key.split(":", 1)[1]: entity[image]
            for key, entity in entities.items()
            if entity["kind"] == name and entity.get(image) is not None
        }
        if not expected:
            continue
        actual = _read_images(connection, kind, expected, lock=lock)
        for key, want in expected.items():
            have = actual.get(key)
            if have is None:
                raise RecoveryError(f"{name}:{key} is missing from the target")
            different = sorted(column for column in want if have.get(column) != want[column])
            if different:
                raise RecoveryError(
                    f"{name}:{key} differs from its {image} image in {', '.join(different)}"
                )


def _write_images(
    connection: psycopg.Connection[tuple[Any, ...]],
    entities: Mapping[str, dict[str, Any]],
    image: str,
) -> None:
    for name, kind in _KINDS.items():
        statement = sql.SQL("UPDATE {} SET {} WHERE {} = %s").format(
            sql.Identifier(kind.table),
            sql.SQL(", ").join(sql.SQL("{} = %s").format(sql.Identifier(c)) for c in kind.columns),
            sql.Identifier(kind.key),
        )
        for key, entity in entities.items():
            if entity["kind"] != name or entity.get(image) is None:
                continue
            values = [entity[image][column] for column in kind.columns]
            connection.execute(statement, (*values, key.split(":", 1)[1]))


_SEALED = (
    ("provider_credentials", "ciphertext", "key_id"),
    ("billing_sources", "ciphertext", "key_id"),
    ("alert_channels", "secret_enc", "secret_key_id"),
    ("audit_streams", "secret_enc", "secret_key_id"),
    ("users", "totp_secret", "totp_key_id"),
)


def _verify(
    connection: psycopg.Connection[tuple[Any, ...]], settings: Settings, app_role: str
) -> dict[str, Any]:
    """Schema-independent facts a restore must satisfy: every sealed value opens with the
    keyring, every audit chain is intact, RLS is forced, the app role is unprivileged, and the
    unlogged rate-limit table is empty (its contents are never dumped)."""
    sealed: dict[str, Any] = {}
    for table, column, key_column in _SEALED:
        key_ids: set[str] = set()
        rows = connection.execute(
            sql.SQL("SELECT {}, {} FROM {} WHERE {} IS NOT NULL").format(
                *map(sql.Identifier, (column, key_column, table, column))
            )
        ).fetchall()
        for ciphertext, key_id in rows:
            try:
                decrypt(Sealed(bytes(ciphertext), key_id), settings=settings)
            except CryptoError as error:
                raise RecoveryError(
                    f"{table}: a value sealed under key {key_id!r} cannot be opened: {error}"
                ) from None
            key_ids.add(key_id)
        sealed[table] = {"count": len(rows), "key_ids": sorted(key_ids)}

    heads: dict[str, dict[str, Any]] = {}
    # psycopg binds `%(name)s`, SQLAlchemy `:name`; the query is otherwise plain SQL.
    chain_query = str(_CHECK_CHAIN).replace(":org_id", "%(org_id)s")
    for (org_id,) in connection.execute("SELECT id FROM organizations ORDER BY id").fetchall():
        checked, _first, head_seq, head_hash, broken = connection.execute(
            chain_query, {"org_id": org_id}
        ).fetchone()  # type: ignore[misc]
        if broken is not None:
            raise RecoveryError(f"audit chain of organization {org_id} is broken at seq {broken}")
        heads[str(org_id)] = {
            "checked": int(checked),
            "head_seq": head_seq,
            "head_hash": None if head_hash is None else bytes(head_hash).hex(),
        }

    rls = connection.execute(
        "SELECT relname, relrowsecurity AND relforcerowsecurity FROM pg_class "
        "WHERE relname IN ('traces', 'spans') AND relkind IN ('r', 'p')"
    ).fetchall()
    if sorted(name for name, forced in rls if forced) != ["spans", "traces"]:
        raise RecoveryError("row-level security is not enabled and forced on traces and spans")
    role = connection.execute(
        "SELECT rolsuper OR rolbypassrls OR NOT rolcanlogin, "
        "has_database_privilege(rolname, current_database(), 'CONNECT') "
        "FROM pg_roles WHERE rolname = %s",
        (app_role,),
    ).fetchone()
    if role is None or role[0] or not role[1]:
        raise RecoveryError(
            f"role {app_role!r} must exist, log in, have no superuser or RLS bypass and connect "
            "to the target; run `spanlight ensure-app-role` against it first"
        )
    (buckets,) = connection.execute("SELECT count(*) FROM rate_limit_buckets").fetchone()  # type: ignore[misc]
    if buckets:
        raise RecoveryError("rate_limit_buckets is not empty; a restored UNLOGGED table must be")
    return {
        "sealed": sealed,
        "audit_heads": heads,
        "rls_forced": ["spans", "traces"],
        "app_role": app_role,
    }


# ---------------------------------------------------------------- commands


def _bind(manifest: Mapping[str, Any], identity: _Identity) -> None:
    target = manifest["target"]
    if (target["endpoint"], target["dbname"], target["oid"]) != (
        identity.endpoint,
        identity.dbname,
        identity.oid,
    ):
        raise RecoveryError("the target is not the database this manifest was prepared for")
    if manifest["schema_revision"] != identity.schema_revision:
        raise RecoveryError("the target's schema revision differs from the manifest's")


def _load_manifest(path: Path) -> tuple[dict[str, Any], str]:
    manifest, sha = read_json(path)
    if manifest.get("format") != MANIFEST_FORMAT:
        raise RecoveryError(f"{path.name} is not a recovery manifest")
    return manifest, sha


def prepare(
    target_url: str,
    manifest_path: Path,
    receipt_path: Path,
    context_path: Path,
    settings: Settings,
    app_role: str,
    now: datetime,
) -> str:
    """Inventory and quarantine the restored work; publish the manifest, then commit.

    Idempotent: with the manifest already published and the database still at its preimages the
    same plan is applied; with the marker and postimages present nothing changes.
    """
    receipt_document, receipt_sha = read_json(receipt_path)
    receipt = _validate(Receipt, receipt_document, receipt_path.name)
    context_document, context_sha = read_json(context_path)
    context = _validate(Context, context_document, context_path.name)
    existing = _load_manifest(manifest_path) if manifest_path.exists() else None

    with _connect(target_url) as connection:
        _lock(connection)
        identity = _identity(connection)
        marker = _parse_marker(identity.comment)
        if existing is not None:
            return _resume_prepare(
                connection, *existing, identity, marker, receipt_sha, context_sha
            )
        if marker is not None:
            raise RecoveryError(
                f"the target already carries recovery marker {marker['id']}; pass that recovery's "
                "manifest to resume it, or restore into another new database"
            )
        if receipt.target.dbname not in (identity.dbname, context.renamed_from):
            raise RecoveryError(
                "the receipt names a different database than the target; record a rename in the "
                "context's renamed_from or use the receipt of this target's restore"
            )
        verification = _verify(connection, settings, app_role)
        recovery_id = str(uuid.uuid4())
        at = _plain(now)
        entities: dict[str, dict[str, Any]] = {}
        for name, kind in _KINDS.items():
            for key, pre in _read_images(connection, kind, None, lock=True).items():
                entity: dict[str, Any] = {
                    "kind": name,
                    "pre": pre,
                    "post": _quarantine(name, pre, recovery_id, at),
                }
                if name == "audit_stream":
                    head = verification["audit_heads"].get(key, {}).get("head_seq")
                    entity["restored_head_seq"] = head or 0
                entities[f"{name}:{key}"] = entity
        for window in context.windows:
            entities[f"window:{window.id}"] = {
                "kind": "window",
                "window": window.model_dump(mode="json"),
            }
        for key, entity in list(entities.items()):
            if entity["kind"] == "experiment" and entity["pre"]["schedule"] is not None:
                for week in _iso_weeks(context.backup_started_at, context.release_at):
                    entities[f"experiment_week:{key.split(':', 1)[1]}:{week}"] = {
                        "kind": "experiment_week"
                    }
        manifest = {
            "format": MANIFEST_FORMAT,
            "recovery_id": recovery_id,
            "created_at": at,
            "operator": context.operator,
            "target": {
                "endpoint": identity.endpoint,
                "dbname": identity.dbname,
                "oid": identity.oid,
            },
            "original_comment": identity.comment,
            "schema_revision": identity.schema_revision,
            "receipt": {
                "sha256": receipt_sha,
                "key": receipt.key,
                "backup_sha256": receipt.sha256,
                "backup_bytes": receipt.bytes,
            },
            "context_sha256": context_sha,
            "release_at": _plain(context.release_at),
            "verification": verification,
            "entities": entities,
        }
        data = canonical_json(manifest)
        write_immutable(manifest_path, data)
        _write_images(connection, entities, "post")
        _set_comment(
            connection,
            identity.dbname,
            f"{MARKER_PREFIX} id={recovery_id} manifest={digest(data)} phase=prepared",
        )
        connection.commit()
    return (
        f"Prepared recovery {recovery_id}: {len(entities)} entities quarantined; "
        f"manifest {digest(data)}."
    )


def _resume_prepare(
    connection: psycopg.Connection[tuple[Any, ...]],
    manifest: dict[str, Any],
    manifest_sha: str,
    identity: _Identity,
    marker: dict[str, str] | None,
    receipt_sha: str,
    context_sha: str,
) -> str:
    _bind(manifest, identity)
    if manifest["receipt"]["sha256"] != receipt_sha or manifest["context_sha256"] != context_sha:
        raise RecoveryError(
            "the receipt or context differs from the one this manifest was prepared with"
        )
    recovery_id = manifest["recovery_id"]
    if marker is None:
        # The manifest was published but the commit never happened: apply the same plan once.
        _compare(connection, manifest["entities"], "pre", lock=True)
        _write_images(connection, manifest["entities"], "post")
        _set_comment(
            connection,
            identity.dbname,
            f"{MARKER_PREFIX} id={recovery_id} manifest={manifest_sha} phase=prepared",
        )
        connection.commit()
        return (
            f"Prepared recovery {recovery_id} from its published manifest; manifest {manifest_sha}."
        )
    if marker["id"] != recovery_id or marker["manifest"] != manifest_sha:
        raise RecoveryError("the target carries a different recovery marker than this manifest")
    _compare(connection, manifest["entities"], "post", lock=False)
    return f"Recovery {recovery_id} is already prepared (phase {marker['phase']}); nothing changed."


def _plan(
    manifest: Mapping[str, Any], manifest_sha: str, decisions_path: Path
) -> tuple[dict[str, Any], str]:
    """Validate the decision file against the manifest and compute every final image.

    Rejects before anything is published: a wrong binding, a duplicate, a missing or unknown
    entity, an `unresolved` disposition, one the entity's kind does not allow, missing or
    mismatching evidence, or a replay that does not accept a possible duplicate.
    """
    document, decisions_sha = read_json(decisions_path)
    decisions = _validate(Decisions, document, decisions_path.name)
    if (
        str(decisions.recovery_id) != manifest["recovery_id"]
        or decisions.manifest_sha256 != manifest_sha
    ):
        raise RecoveryError("the decisions are bound to another recovery or manifest")
    entities = manifest["entities"]
    seen: dict[str, Decision] = {}
    for decision in decisions.decisions:
        if decision.entity in seen:
            raise RecoveryError(f"entity {decision.entity} has more than one decision")
        if decision.entity not in entities:
            raise RecoveryError(f"entity {decision.entity} is not in the manifest")
        seen[decision.entity] = decision
    missing = sorted(set(entities) - set(seen))
    if missing:
        raise RecoveryError(f"{len(missing)} entities have no decision, first {missing[0]}")
    final: dict[str, Any] = {}
    for key, decision in seen.items():
        kind = entities[key]["kind"]
        allowed = _KINDS[kind].allowed if kind in _KINDS else _EXTERNAL[kind]
        if decision.disposition == "unresolved":
            raise RecoveryError(f"entity {key} is still unresolved")
        if decision.disposition not in allowed:
            raise RecoveryError(f"entity {key} ({kind}) does not allow {decision.disposition}")
        if decision.decided_at.tzinfo is None:
            raise RecoveryError(f"entity {key}: decided_at must carry a UTC offset")
        if decision.disposition == "confirmed_completed":
            _check_evidence(decision, decisions_path.parent, needs_sent_at=kind == "notification")
        if decision.disposition == "authorize_replay" and not decision.accepts_duplicate:
            raise RecoveryError(f"entity {key}: authorize_replay must set accepts_duplicate")
        final[key] = _final(
            kind, entities[key], decision, manifest["recovery_id"], manifest["release_at"]
        )
    plan = {
        "format": PLAN_FORMAT,
        "recovery_id": manifest["recovery_id"],
        "manifest_sha256": manifest_sha,
        "decisions_sha256": decisions_sha,
        "release_at": manifest["release_at"],
        "final": final,
    }
    return plan, decisions_sha


def _check_evidence(decision: Decision, directory: Path, *, needs_sent_at: bool) -> None:
    if decision.evidence is None:
        raise RecoveryError(f"entity {decision.entity}: confirmed_completed needs evidence")
    if needs_sent_at and decision.evidence.sent_at is None:
        raise RecoveryError(f"entity {decision.entity}: delivery evidence needs sent_at")
    path = directory / decision.evidence.ref
    if path.is_symlink() or not path.is_file():
        raise RecoveryError(
            f"entity {decision.entity}: evidence {decision.evidence.ref} is not a file"
        )
    if (
        path.stat().st_size > MAX_FILE_BYTES
        or digest(path.read_bytes()) != decision.evidence.sha256
    ):
        raise RecoveryError(
            f"entity {decision.entity}: evidence {decision.evidence.ref} does not match its sha256"
        )


def plan_path(manifest_path: Path) -> Path:
    return manifest_path.with_name(manifest_path.name + ".apply.json")


def _finals(manifest: Mapping[str, Any], plan: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """The manifest's entities with the plan's final image attached, for `_compare`/`_write`."""
    return {
        key: {**entity, "final": plan["final"].get(key)}
        for key, entity in manifest["entities"].items()
    }


def apply(target_url: str, manifest_path: Path, decisions_path: Path) -> str:
    """Apply complete decisions once: validate, publish the plan, write the final images."""
    manifest, manifest_sha = _load_manifest(manifest_path)
    plan, decisions_sha = _plan(manifest, manifest_sha, decisions_path)
    plan_data = canonical_json(plan)
    plan_sha = digest(plan_data)
    applied = (
        f"{MARKER_PREFIX} id={manifest['recovery_id']} manifest={manifest_sha} "
        f"phase=applied decisions={decisions_sha} plan={plan_sha}"
    )
    with _connect(target_url) as connection:
        _lock(connection)
        identity = _identity(connection)
        _bind(manifest, identity)
        marker = _parse_marker(identity.comment)
        if (
            marker is None
            or marker["id"] != manifest["recovery_id"]
            or marker["manifest"] != manifest_sha
        ):
            raise RecoveryError("the target is not prepared for this manifest")
        entities = _finals(manifest, plan)
        if marker["phase"] == "applied":
            if (marker["decisions"], marker["plan"]) != (decisions_sha, plan_sha):
                raise RecoveryError(
                    "the target was applied with different decisions; this plan is not published"
                )
            _compare(connection, entities, "final", lock=False)
            return f"Recovery {manifest['recovery_id']} is already applied; nothing changed."
        _compare(connection, entities, "post", lock=True)
        write_immutable(plan_path(manifest_path), plan_data)
        _write_images(connection, entities, "final")
        _set_comment(connection, identity.dbname, applied)
        connection.commit()
    return (
        f"Applied recovery {manifest['recovery_id']}: decisions {decisions_sha}, plan {plan_sha}."
    )


# Settings a first reopening must run with: every automatic external producer off.
RELEASE_SETTINGS = (
    "experiment_schedules_enabled",
    "evals_enabled",
    "alerts_evaluation_enabled",
    "weekly_digest_enabled",
    "demo_enabled",
    "billing_sources_enabled",
    "provider_status_polling_enabled",
)
_NOTHING_LEFT = (
    ("jobs", "status IN ('queued', 'running')", "queued or running jobs"),
    ("experiment_runs", "status IN ('queued', 'running')", "queued or running experiment runs"),
    ("evaluator_runs", "status IN ('queued', 'running')", "queued or running evaluator runs"),
    ("experiments", "schedule IS NOT NULL", "scheduled experiments"),
    ("evaluators", "enabled", "enabled evaluators"),
    ("alert_rules", "enabled", "enabled alert rules"),
    ("projects", "weekly_digest_enabled", "projects with the weekly digest on"),
    ("audit_streams", "enabled", "enabled audit streams"),
)


def check(
    target_url: str, manifest_path: Path, decisions_path: Path, settings: Settings, app_role: str
) -> list[str]:
    """Read-only release check; the lines to print on success. Raises on any failure."""
    manifest, manifest_sha = _load_manifest(manifest_path)
    plan, decisions_sha = _plan(manifest, manifest_sha, decisions_path)
    plan_sha = digest(canonical_json(plan))
    _published, published_sha = read_json(plan_path(manifest_path))
    if published_sha != plan_sha:
        raise RecoveryError("the published apply plan differs from these decisions")
    off = [name for name in RELEASE_SETTINGS if getattr(settings, name) is not False]
    if off:
        raise RecoveryError(
            f"release settings must be false: {', '.join(name.upper() for name in off)}"
        )
    with _connect(target_url) as connection:
        _lock(connection)
        identity = _identity(connection)
        _bind(manifest, identity)
        marker = _parse_marker(identity.comment)
        if marker is None or marker["phase"] != "applied":
            raise RecoveryError("the target is not in the applied phase")
        if (marker["id"], marker["manifest"], marker["decisions"], marker["plan"]) != (
            manifest["recovery_id"],
            manifest_sha,
            decisions_sha,
            plan_sha,
        ):
            raise RecoveryError("the target's recovery marker does not match these files")
        _compare(connection, _finals(manifest, plan), "final", lock=False)
        verification = _verify(connection, settings, app_role)
        if verification["audit_heads"] != manifest["verification"]["audit_heads"]:
            raise RecoveryError("the audit heads changed since prepare")
        replays = {
            key.split(":", 1)[1]
            for key, final in plan["final"].items()
            if key.startswith("notification:")
            and final is not None
            and final["status"] == "pending"
        }
        pending = connection.execute(
            "SELECT id FROM notification_outbox WHERE status = 'pending'"
        ).fetchall()
        unexpected = sorted(str(row[0]) for row in pending if str(row[0]) not in replays)
        if unexpected:
            raise RecoveryError(
                f"{len(unexpected)} pending notifications are not authorized replays"
            )
        for table, where, what in _NOTHING_LEFT:
            (count,) = connection.execute(
                sql.SQL("SELECT count(*) FROM {} WHERE ").format(sql.Identifier(table))
                + sql.SQL(where)
            ).fetchone()  # type: ignore[misc]
            if count:
                raise RecoveryError(f"the target still has {count} {what}")
        connection.rollback()
    return [
        f"recovery {manifest['recovery_id']}",
        f"manifest {manifest_sha}",
        f"decisions {decisions_sha}",
        f"plan {plan_sha}",
        f"authorized notification replays: {len(replays)}",
        "Reopening checklist (this command starts nothing):",
        "  1. Keep services, producers, deploy hooks and the prune schedule stopped until step 3.",
        "  2. Start workers with the release settings still false; watch for unapproved effects.",
        "  3. Reopen API and gateway ingress only after held upstream retries are reconciled.",
        "  4. Re-enabling schedules, evaluators, alert rules, digests and audit streams is "
        "separate, reviewed work.",
        "Any write to the target after this check invalidates it; run it again before release.",
    ]
