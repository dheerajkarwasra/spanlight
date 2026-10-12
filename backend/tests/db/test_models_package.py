"""The ORM models live in a package split by domain, with one public import path."""

import importlib

from app.db.models import Base

ALL_TABLES = {
    "alert_channels",
    "alert_events",
    "alert_rules",
    "alert_states",
    "annotation_queue_items",
    "annotation_queues",
    "api_keys",
    "audit_events",
    "budgets",
    "dataset_items",
    "datasets",
    "detector_runs",
    "email_tokens",
    "evaluator_runs",
    "evaluators",
    "experiment_items",
    "experiment_runs",
    "experiments",
    "exports",
    "fault_profiles",
    "gateway_cache",
    "gateway_key_minutes",
    "gateway_keys",
    "gateway_route_versions",
    "gateway_routes",
    "idempotency_keys",
    "insight_explanations",
    "insights",
    "invites",
    "jobs",
    "login_attempts",
    "memberships",
    "model_prices",
    "notification_outbox",
    "oauth_identities",
    "organizations",
    "personal_access_tokens",
    "price_overrides",
    "projects",
    "prompt_labels",
    "prompt_versions",
    "prompts",
    "provider_credentials",
    "rate_limit_buckets",
    "recovery_codes",
    "score_configs",
    "scores",
    "sessions",
    "span_rollups_hourly",
    "spans",
    "throttle_events",
    "trace_rollups_hourly",
    "traces",
    "user_stats_daily",
    "users",
    "worker_heartbeats",
}

DOMAIN_MODULES = (
    "alerts",
    "annotations",
    "auth",
    "base",
    "budgets",
    "datasets",
    "end_users",
    "evals",
    "experiments",
    "exports",
    "gateway",
    "identity",
    "insights",
    "notifications",
    "platform",
    "pricing",
    "projects",
    "prompts",
    "rollups",
    "scores",
    "telemetry",
)


def test_models_package_exports_every_table() -> None:
    assert set(Base.metadata.tables.keys()) == ALL_TABLES


def test_domain_modules_exist() -> None:
    for module in DOMAIN_MODULES:
        assert importlib.import_module(f"app.db.models.{module}")
