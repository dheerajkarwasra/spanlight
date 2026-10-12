"""SQLAlchemy ORM models, split by domain.

The Alembic migration is the source of truth for DDL (constraints, RLS
policies, generated columns); these models mirror it for typed queries.

Every public name is re-exported here, so callers import from `app.db.models`
and never from a domain module. Importing the package registers every table on
`Base.metadata`, which Alembic autogenerate relies on.
"""

from app.db.models.alerts import AlertChannel, AlertChannelKind, AlertEvent, AlertRule, AlertState
from app.db.models.annotations import AnnotationQueue, AnnotationQueueItem
from app.db.models.auth import (
    EmailToken,
    EmailTokenKind,
    OAuthIdentity,
    PersonalAccessToken,
    RecoveryCode,
    ThrottleEvent,
    TokenScope,
)
from app.db.models.base import Base
from app.db.models.budgets import Budget
from app.db.models.datasets import Dataset, DatasetItem
from app.db.models.end_users import UserStatsDaily
from app.db.models.evals import (
    Evaluator,
    EvaluatorKind,
    EvaluatorRun,
    EvaluatorRunSource,
    EvaluatorRunStatus,
)
from app.db.models.experiments import (
    Experiment,
    ExperimentItem,
    ExperimentRun,
    ExperimentRunStatus,
)
from app.db.models.exports import Export, ExportFormat, ExportKind, ExportStatus
from app.db.models.gateway import (
    FaultProfile,
    FaultScenario,
    GatewayCacheEntry,
    GatewayKey,
    GatewayKeyMinute,
    GatewayRoute,
    GatewayRouteVersion,
    ProviderCredential,
    ProviderKind,
)
from app.db.models.identity import (
    AuditAction,
    AuditEvent,
    Invite,
    LoginAttempt,
    Membership,
    MembershipRole,
    Organization,
    Session,
    User,
)
from app.db.models.insights import DetectorRun, Insight, InsightExplanation
from app.db.models.notifications import NotificationOutbox, NotificationStatus
from app.db.models.platform import (
    IdempotencyKey,
    Job,
    JobStatus,
    RateLimitBucket,
    WorkerHeartbeat,
)
from app.db.models.pricing import ModelPrice, PriceOverride
from app.db.models.projects import ApiKey, Project
from app.db.models.prompts import Prompt, PromptKind, PromptLabel, PromptVersion
from app.db.models.rollups import SpanRollupHourly, TraceRollupHourly
from app.db.models.scores import Score, ScoreConfig
from app.db.models.telemetry import Span, SpanKind, SpanStatus, Trace

__all__ = [
    "AlertChannel",
    "AlertChannelKind",
    "AlertEvent",
    "AlertRule",
    "AlertState",
    "AnnotationQueue",
    "AnnotationQueueItem",
    "ApiKey",
    "AuditAction",
    "AuditEvent",
    "Base",
    "Budget",
    "Dataset",
    "DatasetItem",
    "DetectorRun",
    "EmailToken",
    "EmailTokenKind",
    "Evaluator",
    "EvaluatorKind",
    "EvaluatorRun",
    "EvaluatorRunSource",
    "EvaluatorRunStatus",
    "Experiment",
    "ExperimentItem",
    "ExperimentRun",
    "ExperimentRunStatus",
    "Export",
    "ExportFormat",
    "ExportKind",
    "ExportStatus",
    "FaultProfile",
    "FaultScenario",
    "GatewayCacheEntry",
    "GatewayKey",
    "GatewayKeyMinute",
    "GatewayRoute",
    "GatewayRouteVersion",
    "IdempotencyKey",
    "Insight",
    "InsightExplanation",
    "Invite",
    "Job",
    "JobStatus",
    "LoginAttempt",
    "Membership",
    "MembershipRole",
    "ModelPrice",
    "NotificationOutbox",
    "NotificationStatus",
    "OAuthIdentity",
    "Organization",
    "PersonalAccessToken",
    "PriceOverride",
    "Project",
    "Prompt",
    "PromptKind",
    "PromptLabel",
    "PromptVersion",
    "ProviderCredential",
    "ProviderKind",
    "RateLimitBucket",
    "RecoveryCode",
    "Score",
    "ScoreConfig",
    "Session",
    "Span",
    "SpanKind",
    "SpanRollupHourly",
    "SpanStatus",
    "ThrottleEvent",
    "TokenScope",
    "Trace",
    "TraceRollupHourly",
    "User",
    "UserStatsDaily",
    "WorkerHeartbeat",
]
