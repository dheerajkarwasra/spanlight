"""Migration 0601 adds the billing sources, their mappings, runs and lines, and can be undone.

A billing source is an organization's view of one provider account (an Admin API key, or CSV
uploads); it maps to the projects it covers, every fetch is a run, and every provider line is a
row keyed by the hash of its identity. The two composite foreign keys of the mapping table make
a source mapped to another organization's project unrepresentable, and every CHECK has its own
name, so an insert that breaks one names it.

Every test here downgrades the session's shared database or writes rows under the truncated
`organizations`, and leaves the schema as it found it.
"""

import re
import uuid
from decimal import Decimal
from hashlib import sha256

import psycopg
import pytest
from sqlalchemy import create_engine, text

from app.db.migrations import downgrade_to, head_revision, upgrade_to_head
from app.db.models import AuditAction
from tests.conftest import admin_database_url

ORG_A = uuid.UUID("10000000-0000-4000-8000-000000000601")
ORG_B = uuid.UUID("10000000-0000-4000-8000-000000000602")
PROJECT_A = uuid.UUID("20000000-0000-4000-8000-000000000601")
PROJECT_B = uuid.UUID("20000000-0000-4000-8000-000000000602")
SOURCE_A = uuid.UUID("30000000-0000-4000-8000-000000000601")
SOURCE_A2 = uuid.UUID("30000000-0000-4000-8000-000000000603")
RUN_A = uuid.UUID("40000000-0000-4000-8000-000000000601")
LINE_KEY = "ab" * 32

TABLES = ("billing_sources", "billing_source_projects", "billing_fetch_runs", "provider_cost_lines")
NEW_AUDIT_ACTIONS = (
    "billing_source.create",
    "billing_source.update",
    "billing_source.rotate",
    "billing_source.delete",
    "billing_source.refresh",
    "billing_source.upload",
)

# (table, column) -> (udt_name, is_nullable, default)
EXPECTED_COLUMNS: dict[tuple[str, str], tuple[str, str, str | None]] = {
    ("billing_sources", "id"): ("uuid", "NO", None),
    ("billing_sources", "org_id"): ("uuid", "NO", None),
    ("billing_sources", "kind"): ("text", "NO", None),
    ("billing_sources", "provider"): ("text", "NO", None),
    ("billing_sources", "name"): ("text", "NO", None),
    ("billing_sources", "ciphertext"): ("bytea", "YES", None),
    ("billing_sources", "key_id"): ("text", "YES", None),
    ("billing_sources", "provider_scope"): ("text", "YES", None),
    ("billing_sources", "enabled"): ("bool", "NO", "true"),
    ("billing_sources", "status"): ("text", "NO", "'never_fetched'::text"),
    ("billing_sources", "last_fetched_at"): ("timestamptz", "YES", None),
    ("billing_sources", "last_success_at"): ("timestamptz", "YES", None),
    ("billing_sources", "last_error"): ("text", "YES", None),
    ("billing_sources", "created_by"): ("uuid", "YES", None),
    ("billing_sources", "created_at"): ("timestamptz", "NO", "now()"),
    ("billing_sources", "updated_at"): ("timestamptz", "NO", "now()"),
    ("billing_source_projects", "source_id"): ("uuid", "NO", None),
    ("billing_source_projects", "project_id"): ("uuid", "NO", None),
    ("billing_source_projects", "org_id"): ("uuid", "NO", None),
    ("billing_source_projects", "provider"): ("text", "NO", None),
    ("billing_fetch_runs", "id"): ("uuid", "NO", None),
    ("billing_fetch_runs", "source_id"): ("uuid", "NO", None),
    ("billing_fetch_runs", "org_id"): ("uuid", "NO", None),
    ("billing_fetch_runs", "trigger"): ("text", "NO", None),
    ("billing_fetch_runs", "day_from"): ("date", "NO", None),
    ("billing_fetch_runs", "day_to"): ("date", "NO", None),
    ("billing_fetch_runs", "status"): ("text", "NO", None),
    ("billing_fetch_runs", "error"): ("text", "YES", None),
    ("billing_fetch_runs", "lines_upserted"): ("int4", "NO", "0"),
    ("billing_fetch_runs", "pages"): ("int4", "NO", "0"),
    ("billing_fetch_runs", "requested_by"): ("uuid", "YES", None),
    ("billing_fetch_runs", "started_at"): ("timestamptz", "NO", "now()"),
    ("billing_fetch_runs", "finished_at"): ("timestamptz", "YES", None),
    ("provider_cost_lines", "id"): ("uuid", "NO", None),
    ("provider_cost_lines", "source_id"): ("uuid", "NO", None),
    ("provider_cost_lines", "org_id"): ("uuid", "NO", None),
    ("provider_cost_lines", "fetch_run_id"): ("uuid", "NO", None),
    ("provider_cost_lines", "day"): ("date", "NO", None),
    ("provider_cost_lines", "provider"): ("text", "NO", None),
    ("provider_cost_lines", "line_key"): ("text", "NO", None),
    ("provider_cost_lines", "line_kind"): ("text", "NO", None),
    ("provider_cost_lines", "model_raw"): ("text", "YES", None),
    ("provider_cost_lines", "model_pattern"): ("text", "YES", None),
    ("provider_cost_lines", "token_type"): ("text", "YES", None),
    ("provider_cost_lines", "service_tier"): ("text", "YES", None),
    ("provider_cost_lines", "context_window"): ("text", "YES", None),
    ("provider_cost_lines", "provider_scope"): ("text", "YES", None),
    ("provider_cost_lines", "description"): ("text", "NO", None),
    ("provider_cost_lines", "quantity"): ("int8", "YES", None),
    ("provider_cost_lines", "amount_usd"): ("numeric", "NO", None),
    ("provider_cost_lines", "first_seen_at"): ("timestamptz", "NO", None),
    ("provider_cost_lines", "last_seen_at"): ("timestamptz", "NO", None),
}

# table -> {constraint name: definition}, for the constraints the spec names or the tests rely on.
EXPECTED_CONSTRAINTS: dict[str, dict[str, str]] = {
    "projects": {"projects_id_org_id_key": "UNIQUE (id, org_id)"},
    "billing_sources": {
        "billing_sources_org_id_name_key": "UNIQUE (org_id, name)",
        "billing_sources_id_org_id_key": "UNIQUE (id, org_id)",
        "billing_sources_id_provider_key": "UNIQUE (id, provider)",
        "billing_sources_org_id_fkey": (
            "FOREIGN KEY (org_id) REFERENCES organizations(id) ON DELETE CASCADE"
        ),
        "billing_sources_created_by_fkey": (
            "FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL"
        ),
    },
    "billing_source_projects": {
        "billing_source_projects_pkey": "PRIMARY KEY (source_id, project_id)",
        "billing_source_projects_project_id_provider_key": "UNIQUE (project_id, provider)",
        "billing_source_projects_source_id_org_id_fkey": (
            "FOREIGN KEY (source_id, org_id) REFERENCES billing_sources(id, org_id) "
            "ON DELETE CASCADE"
        ),
        "billing_source_projects_source_id_provider_fkey": (
            "FOREIGN KEY (source_id, provider) REFERENCES billing_sources(id, provider) "
            "ON DELETE CASCADE"
        ),
        "billing_source_projects_project_id_org_id_fkey": (
            "FOREIGN KEY (project_id, org_id) REFERENCES projects(id, org_id) ON DELETE CASCADE"
        ),
    },
    "billing_fetch_runs": {
        "billing_fetch_runs_source_id_org_id_fkey": (
            "FOREIGN KEY (source_id, org_id) REFERENCES billing_sources(id, org_id) "
            "ON DELETE CASCADE"
        ),
        "billing_fetch_runs_day_range_check": "CHECK ((day_from <= day_to))",
    },
    "provider_cost_lines": {
        "provider_cost_lines_source_id_day_line_key_key": "UNIQUE (source_id, day, line_key)",
        "provider_cost_lines_source_id_org_id_fkey": (
            "FOREIGN KEY (source_id, org_id) REFERENCES billing_sources(id, org_id) "
            "ON DELETE CASCADE"
        ),
        "provider_cost_lines_source_id_provider_fkey": (
            "FOREIGN KEY (source_id, provider) REFERENCES billing_sources(id, provider) "
            "ON DELETE CASCADE"
        ),
        "provider_cost_lines_line_key_check": "CHECK ((line_key ~ '^[0-9a-f]{64}$'::text))",
        "provider_cost_lines_amount_usd_check": (
            "CHECK (((line_kind = 'credit'::text) OR (amount_usd >= (0)::numeric)))"
        ),
    },
}

EXPECTED_INDEXES = {
    "billing_sources_created_by_idx",
    "billing_source_projects_project_id_idx",
    "billing_fetch_runs_source_id_started_at_idx",
    "billing_fetch_runs_requested_by_idx",
    "provider_cost_lines_source_id_day_idx",
    "provider_cost_lines_org_id_day_idx",
    "provider_cost_lines_fetch_run_id_idx",
}


def _columns(database_url: str) -> dict[tuple[str, str], tuple[str, str, str | None]]:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT table_name, column_name, udt_name, is_nullable, column_default
                    FROM information_schema.columns
                    WHERE table_schema = 'public' AND table_name = ANY(:tables)
                    """
                ),
                {"tables": list(TABLES)},
            )
            return {(row[0], row[1]): (row[2], row[3], row[4]) for row in rows}
    finally:
        engine.dispose()


def _constraints(database_url: str, table: str) -> dict[str, str]:
    """Every constraint of `table`: name -> definition; empty when the table does not exist."""
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conrelid = to_regclass(:table)"
                ),
                {"table": table},
            )
            return {row[0]: row[1] for row in rows}
    finally:
        engine.dispose()


def _indexes(database_url: str) -> set[str]:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'")
            )
            return {row[0] for row in rows}
    finally:
        engine.dispose()


def _audit_actions(database_url: str) -> list[str]:
    """The values `audit_events_action_check` lists, in the order of its definition."""
    definition = _constraints(database_url, "audit_events")["audit_events_action_check"]
    return re.findall(r"'([^']+)'::text", definition)


def _current_revision(database_url: str) -> str | None:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            return connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
    finally:
        engine.dispose()


def _admin() -> psycopg.Connection:
    """The superuser sees and writes every row whatever the policies say."""
    libpq_url = admin_database_url().replace("postgresql+psycopg://", "postgresql://")
    return psycopg.connect(libpq_url, autocommit=True)


def _seed_orgs(connection: psycopg.Connection) -> None:
    connection.execute(
        "INSERT INTO organizations (id, name, slug) VALUES (%s, 'A', 'org-a-0601'), "
        "(%s, 'B', 'org-b-0601')",
        (ORG_A, ORG_B),
    )
    connection.execute(
        "INSERT INTO projects (id, org_id, name, slug) VALUES (%s, %s, 'A', 'proj-a-0601'), "
        "(%s, %s, 'B', 'proj-b-0601')",
        (PROJECT_A, ORG_A, PROJECT_B, ORG_B),
    )


SOURCE_DEFAULTS: dict[str, object] = {
    "id": SOURCE_A,
    "org_id": ORG_A,
    "kind": "csv_upload",
    "provider": "anthropic",
    "name": "Prod account",
    "ciphertext": None,
    "key_id": None,
    "status": "never_fetched",
}


def _insert_source(connection: psycopg.Connection, **values: object) -> None:
    row = {**SOURCE_DEFAULTS, **values}
    columns = ", ".join(row)
    placeholders = ", ".join("%s" for _ in row)
    connection.execute(
        f"INSERT INTO billing_sources ({columns}) VALUES ({placeholders})", tuple(row.values())
    )


def _insert_mapping(
    connection: psycopg.Connection,
    source_id: uuid.UUID,
    project_id: uuid.UUID,
    org_id: uuid.UUID,
    provider: str = "anthropic",
) -> None:
    connection.execute(
        "INSERT INTO billing_source_projects (source_id, project_id, org_id, provider) "
        "VALUES (%s, %s, %s, %s)",
        (source_id, project_id, org_id, provider),
    )


def _insert_run(connection: psycopg.Connection, run_id: uuid.UUID = RUN_A) -> None:
    connection.execute(
        "INSERT INTO billing_fetch_runs (id, source_id, org_id, trigger, day_from, day_to, "
        "status) VALUES (%s, %s, %s, 'manual', '2026-10-01', '2026-10-14', 'ok')",
        (run_id, SOURCE_A, ORG_A),
    )


def _insert_line(
    connection: psycopg.Connection,
    line_key: str = LINE_KEY,
    line_kind: str = "tokens",
    amount_usd: Decimal = Decimal("1.5"),
    token_type: str | None = None,
    service_tier: str | None = None,
    model_raw: str | None = None,
    description: str = "claude tokens",
    provider: str = "anthropic",
) -> None:
    connection.execute(
        "INSERT INTO provider_cost_lines (id, source_id, org_id, fetch_run_id, day, provider, "
        "line_key, line_kind, token_type, service_tier, model_raw, description, amount_usd, "
        "first_seen_at, last_seen_at) "
        "VALUES (%s, %s, %s, %s, '2026-10-10', %s, %s, %s, %s, %s, %s, %s, %s, now(), now())",
        (
            uuid.uuid4(),
            SOURCE_A,
            ORG_A,
            RUN_A,
            provider,
            line_key,
            line_kind,
            token_type,
            service_tier,
            model_raw,
            description,
            amount_usd,
        ),
    )


def _assert_at_head(database_url: str) -> None:
    assert _current_revision(database_url) == head_revision()
    columns = _columns(database_url)
    assert columns == EXPECTED_COLUMNS
    for table, expected in EXPECTED_CONSTRAINTS.items():
        actual = _constraints(database_url, table)
        for name, definition in expected.items():
            assert actual.get(name) == definition, (table, name)
    assert _indexes(database_url) >= EXPECTED_INDEXES
    actions = _audit_actions(database_url)
    assert set(actions) == {action.value for action in AuditAction}
    assert tuple(actions[-len(NEW_AUDIT_ACTIONS) :]) == NEW_AUDIT_ACTIONS


def test_0601_tables_and_constraints_at_head(app_database_url: str) -> None:
    _assert_at_head(app_database_url)


def test_0601_round_trip(app_database_url: str) -> None:
    actions_at_head = _audit_actions(app_database_url)
    downgrade_to(app_database_url, "0600")
    try:
        assert _current_revision(app_database_url) == "0600"
        assert _columns(app_database_url) == {}
        for table in TABLES:
            assert _constraints(app_database_url, table) == {}
        assert "projects_id_org_id_key" not in _constraints(app_database_url, "projects")
        assert not EXPECTED_INDEXES & _indexes(app_database_url)
        # The previous CHECK is back: the same list without the six new values.
        assert _audit_actions(app_database_url) == [
            action for action in actions_at_head if action not in NEW_AUDIT_ACTIONS
        ]
    finally:
        # The rest of the session shares this database, so it must end at head. Nothing between
        # 0600 and 0601 touches `model_prices`, so the seed rows need no rebuilding here.
        upgrade_to_head(app_database_url)

    _assert_at_head(app_database_url)


def test_cross_org_mapping_is_unrepresentable() -> None:
    with _admin() as connection:
        _seed_orgs(connection)
        _insert_source(connection)
        # A's source with B's project fails whichever organization the row claims.
        with pytest.raises(
            psycopg.errors.ForeignKeyViolation,
            match="billing_source_projects_project_id_org_id_fkey",
        ):
            _insert_mapping(connection, SOURCE_A, PROJECT_B, ORG_A)
        with pytest.raises(
            psycopg.errors.ForeignKeyViolation,
            match="billing_source_projects_source_id_org_id_fkey",
        ):
            _insert_mapping(connection, SOURCE_A, PROJECT_B, ORG_B)
        # The provider has to be the source's own as well.
        with pytest.raises(
            psycopg.errors.ForeignKeyViolation,
            match="billing_source_projects_source_id_provider_fkey",
        ):
            _insert_mapping(connection, SOURCE_A, PROJECT_A, ORG_A, provider="openai")
        _insert_mapping(connection, SOURCE_A, PROJECT_A, ORG_A)
        # A project is reconciled against one account per provider.
        _insert_source(connection, id=SOURCE_A2, name="Second account")
        with pytest.raises(
            psycopg.errors.UniqueViolation,
            match="billing_source_projects_project_id_provider_key",
        ):
            _insert_mapping(connection, SOURCE_A2, PROJECT_A, ORG_A)


def test_source_checks() -> None:
    sealed = (b"\x00" * 16, "v1")
    with _admin() as connection:
        _seed_orgs(connection)
        _insert_source(connection, kind="anthropic_admin_api", ciphertext=sealed[0], key_id="v1")
        _insert_source(
            connection,
            id=uuid.uuid4(),
            kind="openai_costs_api",
            provider="openai",
            name="OpenAI",
            ciphertext=sealed[0],
            key_id=sealed[1],
        )
        _insert_source(connection, id=uuid.uuid4(), name="CSV", provider="google")
        cases: list[tuple[str, dict[str, object]]] = [
            (
                "billing_sources_kind_provider_check",
                {
                    "kind": "anthropic_admin_api",
                    "provider": "openai",
                    "ciphertext": sealed[0],
                    "key_id": "v1",
                },
            ),
            (
                "billing_sources_kind_ciphertext_check",
                {"kind": "csv_upload", "ciphertext": sealed[0], "key_id": "v1"},
            ),
            ("billing_sources_kind_ciphertext_check", {"kind": "anthropic_admin_api"}),
            (
                "billing_sources_ciphertext_key_id_check",
                {"kind": "anthropic_admin_api", "ciphertext": sealed[0]},
            ),
            ("billing_sources_status_check", {"status": "broken"}),
            ("billing_sources_name_check", {"name": "n" * 129}),
            ("billing_sources_name_check", {"name": ""}),
            ("billing_sources_kind_check", {"kind": "gcp_billing_export"}),
            ("billing_sources_provider_check", {"provider": "azure"}),
            ("billing_sources_org_id_name_key", {"name": "Prod account"}),
            ("billing_sources_provider_scope_check", {"provider_scope": "w" * 129}),
            ("billing_sources_last_error_check", {"last_error": "e" * 201}),
        ]
        for constraint, overrides in cases:
            with pytest.raises(psycopg.errors.IntegrityError, match=constraint):
                _insert_source(connection, **{"id": uuid.uuid4(), "name": "x", **overrides})


def test_run_checks() -> None:
    with _admin() as connection:
        _seed_orgs(connection)
        _insert_source(connection)
        _insert_run(connection)
        bad = (
            ("billing_fetch_runs_trigger_check", "webhook", "2026-10-01", "2026-10-02", "ok"),
            ("billing_fetch_runs_day_range_check", "manual", "2026-10-03", "2026-10-02", "ok"),
            ("billing_fetch_runs_status_check", "manual", "2026-10-01", "2026-10-02", "done"),
        )
        for constraint, trigger, day_from, day_to, status in bad:
            with pytest.raises(psycopg.errors.CheckViolation, match=constraint):
                connection.execute(
                    "INSERT INTO billing_fetch_runs (id, source_id, org_id, trigger, day_from, "
                    "day_to, status) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (uuid.uuid4(), SOURCE_A, ORG_A, trigger, day_from, day_to, status),
                )
        with pytest.raises(psycopg.errors.CheckViolation, match="billing_fetch_runs_error_check"):
            connection.execute(
                "UPDATE billing_fetch_runs SET error = %s WHERE id = %s", ("e" * 201, RUN_A)
            )


def test_line_checks() -> None:
    with _admin() as connection:
        _seed_orgs(connection)
        _insert_source(connection)
        _insert_run(connection)
        _insert_line(connection)
        for token_type in (
            "uncached_input",
            "cache_read",
            "cache_write_5m",
            "cache_write_1h",
            "output",
            "input",
            "cached_input",
        ):
            _insert_line(
                connection, line_key=sha256(token_type.encode()).hexdigest(), token_type=token_type
            )
        with pytest.raises(
            psycopg.errors.CheckViolation, match="provider_cost_lines_line_key_check"
        ):
            _insert_line(connection, line_key="AB" * 32)
        with pytest.raises(
            psycopg.errors.CheckViolation, match="provider_cost_lines_line_key_check"
        ):
            _insert_line(connection, line_key="ab" * 31)
        with pytest.raises(
            psycopg.errors.CheckViolation, match="provider_cost_lines_amount_usd_check"
        ):
            _insert_line(connection, line_key="cd" * 32, amount_usd=Decimal("-1"))
        _insert_line(connection, line_key="ef" * 32, line_kind="credit", amount_usd=Decimal("-1"))
        with pytest.raises(
            psycopg.errors.CheckViolation, match="provider_cost_lines_token_type_check"
        ):
            _insert_line(connection, line_key="12" * 32, token_type="thinking")
        with pytest.raises(
            psycopg.errors.CheckViolation, match="provider_cost_lines_line_kind_check"
        ):
            _insert_line(connection, line_key="34" * 32, line_kind="subscription")
        with pytest.raises(
            psycopg.errors.CheckViolation, match="provider_cost_lines_service_tier_check"
        ):
            _insert_line(connection, line_key="56" * 32, service_tier="fast")
        with pytest.raises(
            psycopg.errors.CheckViolation, match="provider_cost_lines_description_check"
        ):
            _insert_line(connection, line_key="78" * 32, description="d" * 513)
        with pytest.raises(
            psycopg.errors.CheckViolation, match="provider_cost_lines_model_raw_check"
        ):
            _insert_line(connection, line_key="9a" * 32, model_raw="m" * 257)
        # A line's provider is its source's: the composite key refuses any other.
        with pytest.raises(
            psycopg.errors.ForeignKeyViolation, match="provider_cost_lines_source_id_provider_fkey"
        ):
            _insert_line(connection, line_key="bc" * 32, provider="openai")
        with pytest.raises(
            psycopg.errors.UniqueViolation, match="provider_cost_lines_source_id_day_line_key_key"
        ):
            _insert_line(connection)


def test_deleting_a_source_cascades_its_runs_and_lines() -> None:
    """A run cannot be deleted while its lines exist, yet deleting the source removes both."""
    with _admin() as connection:
        _seed_orgs(connection)
        _insert_source(connection)
        _insert_mapping(connection, SOURCE_A, PROJECT_A, ORG_A)
        _insert_run(connection)
        _insert_line(connection)
        with pytest.raises(
            psycopg.errors.ForeignKeyViolation, match="provider_cost_lines_fetch_run_id_fkey"
        ):
            connection.execute("DELETE FROM billing_fetch_runs WHERE id = %s", (RUN_A,))
        connection.execute("DELETE FROM billing_sources WHERE id = %s", (SOURCE_A,))
        for table in TABLES[1:]:
            count = connection.execute(f"SELECT count(*) FROM {table}").fetchone()
            assert count == (0,), table
