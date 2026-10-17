"""Migrations 0800 to 0804 (trace metadata, promoted keys, the attribution rollup, the attribution
export kind and the metadata and trace-name budget scopes) apply, hold their invariants, and can
be undone.

Every test that downgrades does so on the session's shared database and leaves it at head; the
others write rows through the superuser, under the truncated `organizations`.
"""

import json
import uuid

import psycopg
import pytest

from alembic import command
from app.db.migrations import alembic_config, downgrade_to, head_revision, upgrade_to_head
from tests.db import schema_probe as probe

ORG_ID = uuid.UUID("10000000-0000-4000-8000-000000000800")
PROJECT_ID = uuid.UUID("20000000-0000-4000-8000-000000000800")
BELOW_0800 = "0705"
ROLLUPS = "attribution_rollups_daily"
METADATA_CHECK = "traces_metadata_object_check"
METADATA_INDEX = "traces_metadata_idx"
SCOPES_0304 = ["project", "gateway_key", "user", "model"]


def _enum(database_url: str, name: str) -> list[object]:
    rows = probe.query(
        database_url,
        "SELECT e.enumlabel FROM pg_enum AS e JOIN pg_type AS t ON t.oid = e.enumtypid "
        "WHERE t.typname = :name ORDER BY e.enumsortorder",
        name=name,
    )
    return [row[0] for row in rows]


def _functions(database_url: str) -> list[tuple[object, ...]]:
    return probe.query(
        database_url,
        "SELECT proname, pg_get_function_identity_arguments(oid), provolatile, proparallel "
        "FROM pg_proc WHERE proname = 'merge_trace_metadata'",
    )


def _merge(database_url: str, current: dict[str, str], incoming: dict[str, str]) -> dict[str, str]:
    rows = probe.query(
        database_url,
        "SELECT merge_trace_metadata(CAST(:current AS jsonb), CAST(:incoming AS jsonb))",
        current=json.dumps(current),
        incoming=json.dumps(incoming),
    )
    merged = rows[0][0]
    assert isinstance(merged, dict)
    return merged


def _assert_0800_to_0804_present(database_url: str) -> None:
    assert probe.columns(database_url, "traces")["metadata"] == (
        "jsonb",
        "NO",
        None,
        None,
        "'{}'::jsonb",
    )
    assert probe.constraints(database_url, "traces")[METADATA_CHECK] == (
        "CHECK ((jsonb_typeof(metadata) = 'object'::text))",
        True,
    )
    # No index on `metadata`: under FORCE row-level security Postgres cannot use one for `@>`.
    assert METADATA_INDEX not in probe.indexes(database_url, "traces")
    assert _functions(database_url) == [
        ("merge_trace_metadata", "current jsonb, incoming jsonb", "i", "s")
    ]
    assert probe.columns(database_url, "projects")["promoted_metadata_keys"] == (
        "_text",
        "NO",
        None,
        None,
        "'{}'::text[]",
    )
    probe.assert_project_policies(database_url, ROLLUPS)
    assert _enum(database_url, "export_kind") == ["traces", "attribution"]
    assert _enum(database_url, "budget_scope") == [*SCOPES_0304, "metadata", "trace_name"]
    assert probe.constraints(database_url, "budgets")["budgets_scope_id_length_check"] == (
        "CHECK (((char_length(scope_id) >= 1) AND (char_length(scope_id) <= 400)))",
        True,
    )


def _assert_0800_to_0804_absent(database_url: str) -> None:
    assert "metadata" not in probe.columns(database_url, "traces")
    assert METADATA_CHECK not in probe.constraints(database_url, "traces")
    assert METADATA_INDEX not in probe.indexes(database_url, "traces")
    assert _functions(database_url) == []
    assert "promoted_metadata_keys" not in probe.columns(database_url, "projects")
    assert probe.row_security(database_url, ROLLUPS) is None
    assert _enum(database_url, "export_kind") == ["traces"]
    assert _enum(database_url, "budget_scope") == SCOPES_0304
    budget_checks = probe.constraints(database_url, "budgets")
    assert "budgets_scope_id_length_check" not in budget_checks
    assert budget_checks["budgets_scope_id_check"] == (
        "CHECK (((char_length(scope_id) >= 1) AND (char_length(scope_id) <= 200)))",
        True,
    )


def test_0800_column_and_function_at_head(app_database_url: str) -> None:
    assert probe.current_revision(app_database_url) == head_revision()
    _assert_0800_to_0804_present(app_database_url)


def test_0800_check_rejects_a_non_object() -> None:
    with probe.admin() as connection:
        probe.seed_project(connection, ORG_ID, PROJECT_ID)
        with pytest.raises(psycopg.errors.CheckViolation, match=METADATA_CHECK):
            connection.execute(
                "INSERT INTO traces (project_id, trace_id, started_at, ended_at, metadata) "
                "VALUES (%s, %s, now(), now(), '[]'::jsonb)",
                (PROJECT_ID, "0800" * 8),
            )


def test_0800_merge_function_follows_the_merge_rules(app_database_url: str) -> None:
    # Last writer wins per key; an empty batch changes nothing; replaying a batch is idempotent.
    assert _merge(app_database_url, {"a": "1"}, {"a": "2", "b": "3"}) == {"a": "2", "b": "3"}
    assert _merge(app_database_url, {"a": "1"}, {}) == {"a": "1"}
    once = _merge(app_database_url, {"a": "1"}, {"b": "2"})
    assert _merge(app_database_url, once, {"b": "2"}) == once
    # A full trace still updates the keys it has, and adds none.
    full = {f"k{index:02d}": "v" for index in range(32)}
    assert _merge(app_database_url, full, {"k00": "new", "zz": "x"}) == {**full, "k00": "new"}
    # Two free slots go to the new keys first in lexical order.
    thirty = {f"k{index:02d}": "v" for index in range(30)}
    merged = _merge(app_database_url, thirty, {"z": "1", "y": "2", "x": "3"})
    assert merged == {**thirty, "x": "3", "y": "2"}
    # Lexical order is Unicode code-point order, whatever the database's collation: `_` (U+005F)
    # sorts before `b`, and `-` (U+002D) before `1`, so these win the last free slot.
    thirty_one = {f"k{index:02d}": "v" for index in range(31)}
    merged = _merge(app_database_url, thirty_one, {"ab": "1", "a_c": "2"})
    assert merged == {**thirty_one, "a_c": "2"}
    merged = _merge(app_database_url, thirty_one, {"a1": "1", "a-b": "2"})
    assert merged == {**thirty_one, "a-b": "2"}


def test_0800_is_repeatable_after_an_interrupted_run(app_database_url: str) -> None:
    downgrade_to(app_database_url, "0800")
    try:
        before = (
            probe.columns(app_database_url, "traces"),
            probe.constraints(app_database_url, "traces"),
            probe.indexes(app_database_url, "traces"),
            _functions(app_database_url),
        )
        # The autocommit steps landed but the revision was never stamped: run 0800 again.
        with probe.admin() as connection:
            connection.execute("UPDATE alembic_version SET version_num = %s", (BELOW_0800,))
        command.upgrade(alembic_config(app_database_url), "0800")
        assert probe.current_revision(app_database_url) == "0800"
        assert before == (
            probe.columns(app_database_url, "traces"),
            probe.constraints(app_database_url, "traces"),
            probe.indexes(app_database_url, "traces"),
            _functions(app_database_url),
        )
    finally:
        upgrade_to_head(app_database_url)


def test_0800_round_trip(app_database_url: str) -> None:
    _assert_0800_to_0804_present(app_database_url)
    downgrade_to(app_database_url, BELOW_0800)
    try:
        assert probe.current_revision(app_database_url) == BELOW_0800
        _assert_0800_to_0804_absent(app_database_url)
    finally:
        upgrade_to_head(app_database_url)
    _assert_0800_to_0804_present(app_database_url)


def _promote(connection: psycopg.Connection, keys: list[str | None]) -> None:
    connection.execute(
        "UPDATE projects SET promoted_metadata_keys = %s WHERE id = %s", (keys, PROJECT_ID)
    )


def test_0801_promoted_keys_column_and_checks() -> None:
    with probe.admin() as connection:
        probe.seed_project(connection, ORG_ID, PROJECT_ID)
        _promote(connection, ["customer_id", "a.b-c"])
        with pytest.raises(psycopg.errors.CheckViolation, match="promoted_keys_count_check"):
            _promote(connection, [f"key_{index}" for index in range(6)])
        with pytest.raises(psycopg.errors.CheckViolation, match="promoted_keys_pattern_check"):
            _promote(connection, ["Customer"])
        with pytest.raises(psycopg.errors.CheckViolation, match="promoted_keys_pattern_check"):
            _promote(connection, ["k" * 65])
        # The joined form alone cannot tell these from valid lists: a comma inside one element,
        # a NULL element (skipped by `array_to_string`) and a lone empty string.
        for bad in (["a,b"], [None], [""], ["a", None], ["a", ""]):
            with pytest.raises(psycopg.errors.CheckViolation, match="promoted_keys_pattern_check"):
                _promote(connection, bad)
        # A two-dimensional array flattens to a valid joined list, so its shape is checked.
        with pytest.raises(psycopg.errors.CheckViolation, match="promoted_keys_pattern_check"):
            connection.execute(
                "UPDATE projects SET promoted_metadata_keys = '{{a},{b}}' WHERE id = %s",
                (PROJECT_ID,),
            )
        _promote(connection, [])
        _promote(connection, ["customer_id", "a.b-c"])
        stored = connection.execute(
            "SELECT promoted_metadata_keys FROM projects WHERE id = %s", (PROJECT_ID,)
        ).fetchone()
        assert stored == (["customer_id", "a.b-c"],)


def _rollup(connection: psycopg.Connection, **values: object) -> None:
    row: dict[str, object] = {
        "project_id": PROJECT_ID,
        "day": "2026-10-15",
        "environment": None,
        "group_kind": "trace_name",
        "group_key": None,
        "bucket": "value",
        "group_value": "checkout",
        "traces": 1,
        "llm_calls": 1,
        "errors": 0,
        "unpriced_calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        **values,
    }
    placeholders = ", ".join("%s" for _ in row)
    connection.execute(
        f"INSERT INTO {ROLLUPS} ({', '.join(row)}) VALUES ({placeholders})", tuple(row.values())
    )


def test_0802_table_constraints_and_policies(app_database_url: str) -> None:
    probe.assert_project_policies(app_database_url, ROLLUPS)
    natural_key = probe.constraints(app_database_url, ROLLUPS)[f"{ROLLUPS}_natural_key"]
    assert natural_key == (
        "UNIQUE NULLS NOT DISTINCT (project_id, group_kind, group_key, environment, day, "
        "bucket, group_value)",
        True,
    )
    with probe.admin() as connection:
        probe.seed_project(connection, ORG_ID, PROJECT_ID)
        _rollup(connection)
        with pytest.raises(psycopg.errors.UniqueViolation):
            _rollup(connection)
        _rollup(connection, bucket="none", group_value=None)
        with pytest.raises(psycopg.errors.UniqueViolation):
            _rollup(connection, bucket="none", group_value=None)
        _rollup(connection, group_kind="metadata", group_key="a.b-c", environment="prod")
        _rollup(connection, bucket="other", group_value=None, folded_values=3)
        for bad in (
            {"bucket": "other", "group_value": None},
            {"group_kind": "user", "group_key": "customer_id"},
            {"group_kind": "metadata"},
            {"bucket": "value", "group_value": None},
            {"group_kind": "team"},
            {"traces": -1},
            {"cost_usd": -1},
        ):
            with pytest.raises(psycopg.errors.CheckViolation):
                _rollup(connection, **{"group_value": "other-value", **bad})


def _export(connection: psycopg.Connection, kind: str) -> uuid.UUID:
    export_id = uuid.uuid4()
    connection.execute(
        "INSERT INTO exports (id, project_id, kind, format, filters) "
        "VALUES (%s, %s, %s, 'csv', '{}')",
        (export_id, PROJECT_ID, kind),
    )
    return export_id


def test_0803_adds_the_attribution_kind(app_database_url: str) -> None:
    assert _enum(app_database_url, "export_kind") == ["traces", "attribution"]


def test_0803_downgrade_deletes_attribution_exports_and_keeps_trace_exports(
    app_database_url: str,
) -> None:
    with probe.admin() as connection:
        probe.seed_project(connection, ORG_ID, PROJECT_ID)
        traces_export = _export(connection, "traces")
        _export(connection, "attribution")

    downgrade_to(app_database_url, "0802")
    try:
        assert _enum(app_database_url, "export_kind") == ["traces"]
        assert probe.columns(app_database_url, "exports")["kind"][-1] == "'traces'::export_kind"
        with probe.admin() as connection:
            rows = connection.execute("SELECT id FROM exports").fetchall()
            assert rows == [(traces_export,)]
    finally:
        upgrade_to_head(app_database_url)
    assert _enum(app_database_url, "export_kind") == ["traces", "attribution"]


def _budget(connection: psycopg.Connection, scope: str, scope_id: str | None) -> uuid.UUID:
    rule_id, budget_id = uuid.uuid4(), uuid.uuid4()
    connection.execute(
        "INSERT INTO alert_rules (id, project_id, name, kind, metric, comparator, threshold) "
        "VALUES (%s, %s, 'budget', 'budget', 'spend', 'gt', 1)",
        (rule_id, PROJECT_ID),
    )
    connection.execute(
        "INSERT INTO budgets (id, project_id, name, scope, scope_id, period, amount_usd, "
        "action, rule_id) VALUES (%s, %s, %s, %s, %s, 'daily', 1, 'block', %s)",
        (budget_id, PROJECT_ID, f"budget {budget_id}", scope, scope_id, rule_id),
    )
    return rule_id


def test_0804_scopes_and_scope_id_length() -> None:
    with probe.admin() as connection:
        probe.seed_project(connection, ORG_ID, PROJECT_ID)
        _budget(connection, "metadata", "k" * 64 + "=" + "v" * 256)
        _budget(connection, "trace_name", "checkout")
        with pytest.raises(psycopg.errors.CheckViolation, match="budgets_scope_id_length_check"):
            _budget(connection, "user", "u" * 401)


def test_0804_downgrade_deletes_budgets_too_long_for_the_0304_check(app_database_url: str) -> None:
    with probe.admin() as connection:
        probe.seed_project(connection, ORG_ID, PROJECT_ID)
        _budget(connection, "user", "u" * 201)
        kept_rule = _budget(connection, "user", "u" * 200)

    downgrade_to(app_database_url, "0803")
    try:
        with probe.admin() as connection:
            rules = connection.execute("SELECT id FROM alert_rules").fetchall()
            budgets = connection.execute("SELECT rule_id FROM budgets").fetchall()
        assert rules == [(kept_rule,)]
        assert budgets == [(kept_rule,)]
    finally:
        upgrade_to_head(app_database_url)


def test_0804_downgrade_deletes_new_scope_budgets_and_their_rules(app_database_url: str) -> None:
    with probe.admin() as connection:
        probe.seed_project(connection, ORG_ID, PROJECT_ID)
        _budget(connection, "metadata", "customer_id=acme")
        project_rule = _budget(connection, "project", None)

    downgrade_to(app_database_url, "0803")
    try:
        assert _enum(app_database_url, "budget_scope") == SCOPES_0304
        with probe.admin() as connection:
            rules = connection.execute("SELECT id FROM alert_rules").fetchall()
            budgets = connection.execute("SELECT rule_id, scope::text FROM budgets").fetchall()
        assert rules == [(project_rule,)]
        assert budgets == [(project_rule, "project")]
    finally:
        upgrade_to_head(app_database_url)
    assert _enum(app_database_url, "budget_scope") == [*SCOPES_0304, "metadata", "trace_name"]
