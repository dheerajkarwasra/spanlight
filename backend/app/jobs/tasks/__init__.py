"""Registry of background task handlers, keyed by job kind."""

from app.alerts.digest_job import run_weekly_digest
from app.alerts.jobs import run_evaluate_alerts
from app.attribution.jobs import run_refresh_attribution
from app.backups.jobs import run_backup_database
from app.budgets.jobs import REAP_JOB, run_reap_budget_reservations
from app.end_users.erasure_job import ERASE_JOB, run_erase_end_user
from app.end_users.jobs import run_refresh_user_stats, run_refresh_user_stats_deep
from app.evals.experiment_queries import RUN_JOB as EXPERIMENT_RUN_JOB
from app.evals.experiment_run import run_run_experiment
from app.evals.jobs import run_run_evaluator, run_sample_for_evaluation
from app.evals.reservation import RUN_JOB
from app.exports.jobs import run_create_export
from app.exports.purge import PURGE_JOB, run_purge_project_objects
from app.gateway.jobs import run_prune_gateway_cache
from app.insights.jobs import run_detectors
from app.jobs.context import TaskHandler
from app.jobs.tasks.cleanup import run_cleanup_sessions
from app.jobs.tasks.demo_traffic import run_demo_traffic
from app.jobs.tasks.retention import run_retention
from app.jobs.tasks.seed_demo_evals import SEED_DEMO_EVALS_JOB, run_seed_demo_evals
from app.notifications.jobs import run_deliver_notifications
from app.provider_status.jobs import run_poll_provider_status
from app.reconciliation.backfill import BACKFILL_JOB as ROLLUP_COST_BACKFILL_JOB
from app.reconciliation.fetch_jobs import run_fetch_billing_source, run_fetch_billing_sources
from app.reconciliation.rollup_jobs import (
    run_rollup_cost_daily,
    run_rollup_cost_daily_backfill,
    run_rollup_cost_daily_deep,
)
from app.reconciliation.runs import FETCH_SOURCE_JOB
from app.review.job_keys import SELECT_JOB, SELECT_PROJECT_JOB
from app.review.jobs import run_select_for_review, run_select_for_review_project
from app.rollups.jobs import run_rollup_hourly
from app.services.audit_stream import run_deliver_audit_streams

TASKS: dict[str, TaskHandler] = {
    "retention": run_retention,
    "cleanup_sessions": run_cleanup_sessions,
    "prune_gateway_cache": run_prune_gateway_cache,
    "deliver_notifications": run_deliver_notifications,
    "demo_traffic": run_demo_traffic,
    "rollup_hourly": run_rollup_hourly,
    "backup_database": run_backup_database,
    "create_export": run_create_export,
    "evaluate_alerts": run_evaluate_alerts,
    "weekly_digest": run_weekly_digest,
    "run_detectors": run_detectors,
    "refresh_user_stats": run_refresh_user_stats,
    "sample_for_evaluation": run_sample_for_evaluation,
    RUN_JOB: run_run_evaluator,
    EXPERIMENT_RUN_JOB: run_run_experiment,
    PURGE_JOB: run_purge_project_objects,
    SEED_DEMO_EVALS_JOB: run_seed_demo_evals,
    "refresh_user_stats_deep": run_refresh_user_stats_deep,
    "rollup_cost_daily": run_rollup_cost_daily,
    "rollup_cost_daily_deep": run_rollup_cost_daily_deep,
    ROLLUP_COST_BACKFILL_JOB: run_rollup_cost_daily_backfill,
    "fetch_billing_sources": run_fetch_billing_sources,
    FETCH_SOURCE_JOB: run_fetch_billing_source,
    "poll_provider_status": run_poll_provider_status,
    "refresh_attribution": run_refresh_attribution,
    SELECT_JOB: run_select_for_review,
    SELECT_PROJECT_JOB: run_select_for_review_project,
    "deliver_audit_streams": run_deliver_audit_streams,
    REAP_JOB: run_reap_budget_reservations,
    ERASE_JOB: run_erase_end_user,
}
