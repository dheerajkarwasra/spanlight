"""Registry of background task handlers, keyed by job kind."""

from app.alerts.digest_job import run_weekly_digest
from app.alerts.jobs import run_evaluate_alerts
from app.backups.jobs import run_backup_database
from app.end_users.jobs import run_refresh_user_stats
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
from app.notifications.jobs import run_deliver_notifications
from app.rollups.jobs import run_rollup_hourly

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
}
