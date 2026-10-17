"""SQLAlchemy ORM models, split by domain.

The Alembic migration is the source of truth for DDL (constraints, RLS
policies, generated columns); these models mirror it for typed queries.

Every public name is re-exported here, so callers import from `app.db.models`
and never from a domain module. Importing the package registers every table on
`Base.metadata`, which Alembic autogenerate relies on.
"""

from app.db.models.alerts import AlertChannel, AlertChannelKind, AlertEvent, AlertRule, AlertState
from app.db.models.annotations import AnnotationQueue, AnnotationQueueItem
from app.db.models.attribution import AttributionRollupDaily
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
from app.db.models.billing import (
    BillingFetchRun,
    BillingProvider,
    BillingSource,
    BillingSourceKind,
    BillingSourceProject,
    FetchTrigger,
    LineKind,
    ProviderCostLine,
    RunStatus,
    SourceStatus,
    TokenType,
)
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
from app.db.models.insights import DetectorRun, Insight, InsightExplanation, InsightFeedback
from app.db.models.notifications import NotificationOutbox, NotificationStatus
from app.db.models.platform import (
    IdempotencyKey,
    Job,
    JobStatus,
    RateLimitBucket,
    WorkerHeartbeat,
)
from app.db.models.pricing import ModelPrice, PriceOverride, ServerToolFee
from app.db.models.projects import ApiKey, Project
from app.db.models.prompts import Prompt, PromptKind, PromptLabel, PromptVersion
from app.db.models.provider_status import ProviderIncident, ProviderStatusSource
from app.db.models.replays import ReplayFidelity, ReplayRun, ReplayStatus
from app.db.models.review import ReviewPolicy, ReviewSelection
from app.db.models.rollups import (
    CostRollupDaily,
    ErrorRollupHourly,
    SpanRollupHourly,
    TraceRollupHourly,
)
from app.db.models.scores import Score, ScoreConfig
from app.db.models.telemetry import Span, SpanKind, SpanStatus, Trace
from app.db.models.telemetry_losses import SdkExporterCounter, TelemetryLossHourly

__all__ = [
    "AlertChannel",
    "AlertChannelKind",
    "AlertEvent",
    "AlertRule",
    "AlertState",
    "AnnotationQueue",
    "AnnotationQueueItem",
    "ApiKey",
    "AttributionRollupDaily",
    "AuditAction",
    "AuditEvent",
    "Base",
    "BillingFetchRun",
    "BillingProvider",
    "BillingSource",
    "BillingSourceKind",
    "BillingSourceProject",
    "Budget",
    "CostRollupDaily",
    "Dataset",
    "DatasetItem",
    "DetectorRun",
    "EmailToken",
    "EmailTokenKind",
    "ErrorRollupHourly",
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
    "FetchTrigger",
    "GatewayCacheEntry",
    "GatewayKey",
    "GatewayKeyMinute",
    "GatewayRoute",
    "GatewayRouteVersion",
    "IdempotencyKey",
    "Insight",
    "InsightExplanation",
    "InsightFeedback",
    "Invite",
    "Job",
    "JobStatus",
    "LineKind",
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
    "ProviderCostLine",
    "ProviderCredential",
    "ProviderIncident",
    "ProviderKind",
    "ProviderStatusSource",
    "RateLimitBucket",
    "RecoveryCode",
    "ReplayFidelity",
    "ReplayRun",
    "ReplayStatus",
    "ReviewPolicy",
    "ReviewSelection",
    "RunStatus",
    "Score",
    "ScoreConfig",
    "SdkExporterCounter",
    "ServerToolFee",
    "Session",
    "SourceStatus",
    "Span",
    "SpanKind",
    "SpanRollupHourly",
    "SpanStatus",
    "TelemetryLossHourly",
    "ThrottleEvent",
    "TokenScope",
    "TokenType",
    "Trace",
    "TraceRollupHourly",
    "User",
    "UserStatsDaily",
    "WorkerHeartbeat",
]
