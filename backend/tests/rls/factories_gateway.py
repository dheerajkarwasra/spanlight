"""Row factories for the gateway, alert and budget tables."""

from tests.rls.row_spec import HEX16, HEX32, SLUG, RowFactory, parent

# A stored gateway key prefix: `spl_gw_` and 12 characters of lowercase base32 (`[a-z2-7]`).
GATEWAY_PREFIX = "'spl_gw_' || translate(left(md5(random()::text), 12), '0189', 'wxyz')"
# A route config in its stored shape (`load_stored` reads it back), with no targets.
ROUTE_CONFIG = """'{"targets": [], "retry": {}, "fallback": {"on": []}, "timeout_ms": 60000}'"""

GATEWAY = (
    RowFactory(
        table="gateway_routes",
        sql=(
            "INSERT INTO gateway_routes (id, project_id, name, config) "
            f"VALUES (gen_random_uuid(), :project_id, {SLUG}, {ROUTE_CONFIG}) RETURNING *"
        ),
        touch="config",
    ),
    RowFactory(
        table="gateway_route_versions",
        sql=(
            "INSERT INTO gateway_route_versions (route_id, version, project_id, config) "
            "VALUES (:route_id, 1, :project_id, '{}') RETURNING *"
        ),
        touch="config",
        parents=(parent("gateway_routes", route_id="id"),),
    ),
    RowFactory(
        table="fault_profiles",
        sql=(
            "INSERT INTO fault_profiles (id, project_id, name, scenario) "
            f"VALUES (gen_random_uuid(), :project_id, {SLUG}, 'timeout') RETURNING *"
        ),
        touch="params",
    ),
    RowFactory(
        table="gateway_keys",
        sql=(
            "INSERT INTO gateway_keys (id, project_id, route_id, name, prefix, secret_hash, "
            "environment) "
            f"VALUES (gen_random_uuid(), :project_id, :route_id, 'key', {GATEWAY_PREFIX}, "
            "'\\x00'::bytea, 'test') RETURNING *"
        ),
        touch="name",
        parents=(parent("gateway_routes", route_id="id"),),
    ),
    RowFactory(
        table="gateway_key_minutes",
        sql=(
            "INSERT INTO gateway_key_minutes (key_id, minute_start, project_id) "
            "VALUES (:key_id, date_trunc('minute', now()), :project_id) RETURNING *"
        ),
        touch="requests",
        parents=(parent("gateway_keys", key_id="id"),),
    ),
    RowFactory(
        table="gateway_cache",
        sql=(
            "INSERT INTO gateway_cache (project_id, cache_key, model, body, content_type, usage, "
            "expires_at) "
            f"VALUES (:project_id, decode({HEX32} || {HEX32}, 'hex'), 'model', '\\x'::bytea, "
            "'application/json', '{}', now() + interval '1 hour') RETURNING *"
        ),
        touch="hit_count",
    ),
    RowFactory(
        table="alert_rules",
        sql=(
            "INSERT INTO alert_rules (id, project_id, name, kind, metric, comparator, "
            "window_minutes, threshold) "
            "VALUES (gen_random_uuid(), :project_id, 'rule', 'threshold', 'error_rate', 'gt', "
            "5, 1) RETURNING *"
        ),
        touch="enabled",
    ),
    RowFactory(
        table="alert_states",
        sql=(
            "INSERT INTO alert_states (rule_id, project_id, state) "
            "VALUES (:rule_id, :project_id, 'ok') RETURNING *"
        ),
        touch="state",
        parents=(parent("alert_rules", rule_id="id"),),
    ),
    RowFactory(
        table="alert_events",
        sql=(
            "INSERT INTO alert_events (id, project_id, rule_id, state, started_at) "
            "VALUES (gen_random_uuid(), :project_id, :rule_id, 'firing', now()) RETURNING *"
        ),
        touch="payload",
        parents=(parent("alert_rules", rule_id="id"),),
    ),
    RowFactory(
        table="budgets",
        sql=(
            "INSERT INTO budgets (id, project_id, name, scope, period, amount_usd, action, "
            "rule_id) "
            f"VALUES (gen_random_uuid(), :project_id, {SLUG}, 'project', 'daily', 1, 'notify', "
            ":rule_id) RETURNING *"
        ),
        touch="enabled",
        parents=(parent("alert_rules", rule_id="id"),),
    ),
    RowFactory(
        table="budget_counters",
        sql=(
            "INSERT INTO budget_counters (budget_id, period_start, project_id) "
            "VALUES (:budget_id, now(), :project_id) RETURNING *"
        ),
        touch="updated_at",
        parents=(parent("budgets", budget_id="id"),),
    ),
    RowFactory(
        table="budget_reservations",
        sql=(
            "INSERT INTO budget_reservations (id, project_id, budget_id, period_start, trace_id, "
            "span_id, amount_usd, expires_at) "
            f"VALUES (gen_random_uuid(), :project_id, :budget_id, now(), {HEX32}, {HEX16}, 1, "
            "now()) RETURNING *"
        ),
        touch="status",
        parents=(parent("budgets", budget_id="id"),),
    ),
)
