"""Application settings loaded from the environment (and an optional `.env` file)."""

from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from app.core.crypto import parse_keyring
from app.ee.license import parse_license

DEV_SECRET_KEY = "dev-only-insecure-secret-key-change-me"  # noqa: S105 - documented dev default
# Base size of the api's request pool (`app.main._request_engine`); it may overflow by as much
# again. `gateway_record_concurrency` is bounded by it, so the writes that share the pool can
# never take every connection requests need.
REQUEST_POOL_SIZE = 10

OAuthProviderName = Literal["github", "google"]
# The order providers are listed in.
OAUTH_PROVIDER_NAMES: tuple[OAuthProviderName, ...] = ("github", "google")


class Settings(BaseSettings):
    # Settings holds secrets (database password, SECRET_KEY, CREDENTIALS_KEYS). Without this a
    # ValidationError prints a shortened copy of the offending input, and that text goes to logs.
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", hide_input_in_errors=True
    )

    database_url: str = "postgresql+psycopg://spanlight_app:spanlight_app@localhost:5432/spanlight"
    app_base_url: str = "http://localhost:8000"
    allowed_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)
    secret_key: SecretStr = SecretStr(DEV_SECRET_KEY)
    metrics_token: SecretStr | None = None
    # Swagger UI at /api/docs loads its script from a CDN onto the app's origin. Unset: on for
    # http (local development), off for https. /api/openapi.json is served either way.
    api_docs: bool | None = None
    # "<key_id>:<base64 of 32 bytes>[,<key_id>:<base64>...]"; the first entry encrypts new data.
    # Blank leaves application-level encryption off (see app/core/crypto.py).
    credentials_keys: SecretStr | None = None
    # An enterprise license key (`spl_lic_...`, see app/ee/license.py). Blank runs the community
    # edition; a key that does not verify stops startup, an expired one turns ee features off.
    ee_license_key: SecretStr | None = None
    # The version of the hosted terms (`frontend/src/features/legal/content/terms.tsx` `VERSION`);
    # leave unset on self-hosted instances. Unset, no terms are asked for anywhere.
    terms_version: str | None = None

    anthropic_api_key: SecretStr | None = None
    demo_monthly_budget_usd: Decimal = Decimal("1.00")
    demo_enabled: bool | None = None

    # Email is optional; EMAIL_PROVIDER picks how it is sent. `console` writes nothing anywhere
    # unless EMAIL_CONSOLE_FILE is set, and then counts as configured (see `is_email_configured`).
    email_provider: Literal["console", "resend", "smtp"] = "console"
    email_from: str | None = None
    resend_api_key: SecretStr | None = None
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_starttls: bool = True
    email_console_file: Path | None = None

    # Sign in with GitHub / Google. A provider is on exactly when both of its values are set
    # (see `oauth_providers`); setting only one of the pair is refused at startup.
    oauth_github_client_id: str | None = None
    oauth_github_client_secret: SecretStr | None = None
    oauth_google_client_id: str | None = None
    oauth_google_client_secret: SecretStr | None = None

    # S3-compatible object storage (AWS S3, MinIO, R2, ...) for exports. It is on
    # exactly when the bucket and both keys are set (see `is_object_storage_configured`).
    # S3_ENDPOINT is only needed for non-AWS services; S3_FORCE_PATH_STYLE addresses buckets as
    # `<endpoint>/<bucket>` instead of `<bucket>.<endpoint>`, which MinIO and most self-hosted
    # services require.
    s3_endpoint: str | None = None
    # The address browsers reach the store at, when that differs from S3_ENDPOINT (for example
    # `http://minio:9000` inside Docker, `http://localhost:9000` outside). Download links are
    # signed against it; everything the server itself does uses S3_ENDPOINT.
    s3_public_endpoint: str | None = None
    s3_bucket: str | None = None
    s3_region: str | None = None
    s3_access_key: SecretStr | None = None
    s3_secret_key: SecretStr | None = None
    s3_force_path_style: bool = False

    # Backup storage never falls back to export or ambient credentials.
    backup_s3_endpoint: str | None = None
    backup_s3_bucket: str | None = None
    backup_s3_region: str | None = None
    backup_s3_access_key: SecretStr | None = None
    backup_s3_secret_key: SecretStr | None = None
    backup_s3_force_path_style: bool = False
    # Server-side encryption asked for on every backup upload (`AES256` is SSE-S3). Blank sends no
    # header: MinIO without a KMS rejects SSE-S3, so a bucket there must be encrypted another way.
    backup_s3_sse: str | None = "AES256"

    # Nightly `pg_dump` to object storage. The job runs only when BACKUPS_ENABLED is true, an
    # backup store is configured and BACKUP_DATABASE_URL is set; otherwise it ends `done` with
    # outcome `skipped_not_configured`. The tenant tables have row-level security forced on and
    # `pg_dump` turns it off for the dump, which fails unless the role can bypass it. So the URL
    # must use a superuser or a role with BYPASSRLS, never the app role (which cannot, by design).
    # Managed Postgres usually has no true superuser: create a role with BYPASSRLS there.
    backups_enabled: bool = True
    backup_database_url: SecretStr | None = None

    # Whether `/health/ready` requires a live worker: it answers 503 when no worker has beaten
    # in the last 120 seconds. Turn it off for a deployment that deliberately runs the API alone
    # (the heartbeat age is still reported). Notifications and exports only move while a worker
    # runs.
    worker_required: bool = True
    # Port on which the worker serves /metrics (same METRICS_TOKEN rule as the api). Blank leaves
    # the listener off: the worker's job, outbox, notification and rollup metrics are only on it.
    worker_metrics_port: int | None = Field(default=None, ge=1, le=65535)

    # One monotonic deadline from the first stop signal; later signals never extend it.
    shutdown_timeout_seconds: float = Field(default=60, gt=0, allow_inf_nan=False)
    shutdown_work_seconds: float = Field(default=50, gt=0, allow_inf_nan=False)
    shutdown_flush_seconds: float = Field(default=3, gt=0, allow_inf_nan=False)
    shutdown_close_seconds: float = Field(default=2, gt=0, allow_inf_nan=False)

    # How long an API request may wait for a connection from the main pool, and how long any one
    # of its statements may run. Past either limit the request fails fast with 503 and a
    # `Retry-After`, instead of queueing behind slow work until every other request is stuck too.
    # The worker and the streaming exports are not subject to them.
    api_pool_timeout_seconds: float = Field(default=1.0, ge=1, le=60)
    api_statement_timeout_seconds: float = Field(default=10.0, ge=1, le=300)
    # How many `/api` and `/v1` requests one process serves at once; past it a request answers
    # 503 with `Retry-After: 1` at once instead of queueing (`app.core.admission`). About three
    # times the main pool (10 + 10 overflow).
    api_max_in_flight: int = Field(default=64, ge=1)
    # How long one api process reuses a project API key's row, and a gateway key's context
    # (project, route, credential, lab profile, capture setting), before reading it again
    # (`app.api.key_cache`). A change made through another process takes effect here within this
    # long; 0 reads them on every request.
    api_key_cache_ttl_seconds: float = Field(default=10, ge=0)

    # Per-process share of ingest requests, held after key checks and before reading the body.
    # Refusals answer 429 with `Retry-After: 1`; their span count is unknown.
    ingest_max_in_flight_per_project: int = Field(default=8, ge=1)

    # Per-project ingest caps on `POST /v1/traces` and `POST /v1/otlp/traces`, each `0` for no
    # cap: sustained spans a second (bursts of at least a full 1,000-span batch), spans admitted
    # per UTC day and decompressed request body bytes per UTC day. Over a cap the request is
    # `429` with `Retry-After`; refused spans are counted and shown to the project. A project's
    # own values are set with `spanlight set-ingest-caps`. The gateway's own spans are exempt.
    ingest_spans_per_second: int = Field(default=1000, ge=0)
    ingest_spans_per_day: int = Field(default=1_000_000, ge=0)
    ingest_bytes_per_day: int = Field(default=5 * 1024**3, ge=0)

    # The pool that idempotency keys use, apart from the main one. Reserving, completing and
    # releasing a key each take a connection for a few milliseconds while the request itself
    # holds one from the main pool; sharing that pool would let enough concurrent requests wait
    # on each other for it. This pool never nests, so the worst case is a short queue, and
    # `idempotency_pool_timeout_seconds` is how long a request waits before failing instead.
    idempotency_pool_size: int = Field(default=5, ge=1)
    idempotency_pool_timeout_seconds: float = Field(default=5.0, gt=0)

    # The pool that rate limiting uses, for the same reason: each check takes a connection for a
    # millisecond or so, outside the request's own transaction. A check that cannot get one
    # within `rate_limit_pool_timeout_seconds` is skipped (the request goes through), so this is
    # kept short: a saturated pool adds at most this much to a request.
    rate_limit_pool_size: int = Field(default=5, ge=1)
    rate_limit_pool_timeout_seconds: float = Field(default=0.25, gt=0)
    # Connection defaults bound token-bucket SQL only, independently of pool checkout.
    rate_limit_lock_timeout_ms: int = Field(default=100, gt=0)
    rate_limit_statement_timeout_ms: int = Field(default=250, gt=0)

    # Where the LLM gateway (`/gw/v1/*`) runs. `embedded` serves it from the api process;
    # `standalone` leaves it to a separate `python -m app.gateway` process (same image) and
    # `disabled` turns it off. In both of those the api answers `/gw/*` with 404.
    gateway_mode: Literal["embedded", "standalone", "disabled"] = "embedded"
    # HTTP ingress only, held through stream cleanup; replicas multiply each allowance. Local
    # calibration: 32 concurrent 256-byte streams peaked at 195 MB RSS (184 MB base, about
    # 0.35 MB a slot), 8 concurrent ~10 MiB bodies at 446 MB (about 32 MB a slot). Memory follows
    # body bytes, so the byte budget (declared Content-Length, or the full request cap when a body
    # is chunked or undeclared; GET/HEAD/OPTIONS reserve nothing; 80 MiB is the old 8 x 10 MiB
    # worst case) bounds it, the count cap stays as a backstop, and one key may hold at most a
    # share of the slots. A slot outlives the client's last byte by the call's finalization
    # (p50 28 ms, max 141 ms measured), so in-flight calls exceed the clients: 200 looping
    # streaming clients on two keys reached 256 in flight and 128 on one key, and 512 / 256 is
    # what refused none of them, at a 207 MiB RSS peak.
    gateway_max_in_flight: int = Field(default=512, ge=1)
    # The floor is app.api.gw.MAX_REQUEST_BYTES (importing it here would be circular): the
    # budget must fit one full-size body.
    gateway_max_in_flight_bytes: int = Field(default=83_886_080, ge=10_485_760)
    gateway_max_in_flight_per_key: int = Field(default=256, ge=1)
    # Whether provider credentials may point at `http://` base URLs and at private, loopback or
    # link-local addresses. Off, a base URL must be `https://` and resolve only to public
    # addresses, so a credential cannot turn the gateway into a way into the private network.
    # Meant for a self-hosted instance with a local model server; keep it off on a shared one.
    gateway_allow_insecure_base_urls: bool = False
    # Requests per minute one organization may send through the gateway, across all its keys,
    # checked before each key's own limits. Blank means no ceiling. A shared deployment sets it
    # so one organization's traffic cannot crowd out the others'.
    gateway_org_rpm_ceiling: int | None = Field(default=None, ge=1, le=100_000)
    # Gateway spans are written after the answer, in the background, a failed write retried a
    # few times (`app.gateway.recorder.RECORD_RETRY_DELAYS_S`). At most this many writes run at
    # once, and at most `gateway_record_backlog` wait or run; past that a span is dropped and
    # counted. Each write holds a connection of the request pool (10, plus 10 on overflow), and
    # the writes of one project serialize on its dirty-hour row until each commits, so the bound
    # stays below the pool's base size: with more writers a slow commit convoys the rest, they
    # take the connections gateway calls need, and every call stalls. A waiting span
    # holds its input and output already cut to 1 MiB each, and the waiting spans' payloads
    # together stay within `gateway_record_backlog_mb` of stored JSON (the process holds more
    # than that per span, so size both by measuring).
    gateway_record_concurrency: int = Field(default=8, ge=1, le=REQUEST_POOL_SIZE)
    gateway_record_backlog: int = Field(default=5000, ge=1, le=100_000)
    gateway_record_backlog_mb: int = Field(default=128, ge=1, le=65_536)

    # Alerts. `alerts_evaluation_enabled` schedules the alert evaluation job (every minute) and
    # `weekly_digest_enabled` the Monday digest email; turn either off to stop it everywhere.
    alerts_evaluation_enabled: bool = True
    weekly_digest_enabled: bool = True
    # Whether webhook alert channels may point at `http://` URLs and at private, loopback or
    # link-local addresses. Off, a webhook URL must be `https://` and resolve only to public
    # addresses, so a channel cannot turn the worker into a way into the private network.
    webhook_allow_private_targets: bool = False
    # Whether email alert channels may send to any address. Off, every recipient must be a
    # member of the organization with a verified email, so a shared instance cannot be used to
    # mail strangers from its domain. Meant for a self-hosted instance.
    alert_email_any_recipient: bool = False
    # Where PagerDuty alert channels send events (the Events API v2 endpoint).
    pagerduty_events_url: str = "https://events.pagerduty.com/v2/enqueue"

    # The Doctor. `detectors_enabled` schedules the detector job (every 15 minutes) and
    # `user_stats_enabled` the per-user daily stats refresh; turn either off to stop it
    # everywhere. "Explain with Claude" calls `explain_model` through the organization's own
    # Anthropic credential, at most `explain_monthly_budget_usd` per organization per UTC month
    # (0 turns explanations off). The model must have a price, or the budget cannot be enforced.
    detectors_enabled: bool = True
    # Polls the providers' public status pages every 5 minutes (fixed URLs, never a setting).
    # Off, nothing is fetched (an air-gapped instance) and the incident list says so.
    provider_status_polling_enabled: bool = True
    explain_model: str = Field(default="claude-sonnet-5-5", min_length=1)
    explain_monthly_budget_usd: Decimal = Field(default=Decimal("1.00"), ge=0)
    user_stats_enabled: bool = True
    # Capture remains active when consumption is disabled.
    aggregate_repair_enabled: bool = False

    # Evaluations. `evals_enabled` schedules online evaluation (sampling recent spans for the
    # enabled evaluators every minute); off, evaluators only run from experiments and test runs.
    # `eval_sampler_max_runs_per_cycle` caps the runs one evaluator gets per cycle, and
    # `experiment_max_items` is the ceiling of an experiment's `max_items`.
    evals_enabled: bool = True
    eval_sampler_max_runs_per_cycle: int = Field(default=50, ge=1, le=10_000)
    experiment_max_items: int = Field(default=1000, ge=1, le=100_000)
    # `experiment_schedules_enabled` schedules the daily job that reruns every experiment marked
    # `weekly` once per ISO week (unattended, paid calls under each run's cost cap). Off by
    # default: a saved schedule is kept but no run starts from it.
    experiment_schedules_enabled: bool = False

    # Review loop. `review_selection_enabled` schedules the nightly selection of traces into each
    # project's review queue; off, a policy can still be run by hand. `replay_monthly_budget_usd`
    # caps replays per project per UTC month (0 turns replay off with 409 NOT_CONFIGURED).
    review_selection_enabled: bool = True
    replay_monthly_budget_usd: Decimal = Field(default=Decimal("5.00"), ge=0)

    # Billing sources. `billing_sources_enabled` schedules the daily fetch of connected provider
    # cost reports; off, no provider cost API is called anywhere (an air-gapped instance), the
    # routes stay and a manual refresh answers 409 NOT_CONFIGURED.
    billing_sources_enabled: bool = True

    # Audit log. Events older than `audit_retention_days` are deleted by the hourly retention job;
    # 0 keeps every event. 365 is the 12 months PCI DSS 10.5.1 asks for.
    audit_retention_days: int = Field(default=365, ge=0)

    # Attribution. `attribution_enabled` schedules the attribution refresh (every 15 minutes),
    # which rebuilds the daily rollups the attribution view reads over windows longer than a
    # day; off, those windows go stale while windows of 24 hours or less stay live.
    attribution_enabled: bool = True

    sentry_dsn: str | None = None

    # Tracing of the app's own requests, queries and outbound calls. Set the base URL of an OTLP
    # HTTP collector (spans go to `<endpoint>/v1/traces`); without it tracing is off. A value that
    # ends in `/traces` is used as it is, and Spanlight's own `/v1/otlp` base gets `/traces`.
    otel_exporter_otlp_endpoint: str | None = None
    otel_service_name: str = "spanlight-api"

    log_level: str = "INFO"
    log_json: bool = True

    @field_validator(
        "metrics_token",
        "api_docs",
        "worker_metrics_port",
        "gateway_org_rpm_ceiling",
        "anthropic_api_key",
        "demo_enabled",
        "sentry_dsn",
        "otel_exporter_otlp_endpoint",
        "email_from",
        "resend_api_key",
        "smtp_host",
        "smtp_username",
        "smtp_password",
        "email_console_file",
        "oauth_github_client_id",
        "oauth_github_client_secret",
        "oauth_google_client_id",
        "oauth_google_client_secret",
        "backup_s3_endpoint",
        "backup_s3_bucket",
        "backup_s3_region",
        "backup_s3_access_key",
        "backup_s3_secret_key",
        "backup_s3_sse",
        "s3_endpoint",
        "s3_public_endpoint",
        "s3_bucket",
        "s3_region",
        "s3_access_key",
        "s3_secret_key",
        "backup_database_url",
        "terms_version",
        mode="before",
    )
    @classmethod
    def _blank_is_unset(cls, value: object) -> object:
        # `KEY=` in a .env file means "not configured", not "the empty string".
        return None if value == "" else value

    @field_validator(
        "email_provider",
        "backup_s3_force_path_style",
        "smtp_port",
        "smtp_starttls",
        "shutdown_timeout_seconds",
        "shutdown_work_seconds",
        "shutdown_flush_seconds",
        "shutdown_close_seconds",
        "api_pool_timeout_seconds",
        "api_statement_timeout_seconds",
        "api_max_in_flight",
        "api_key_cache_ttl_seconds",
        "ingest_max_in_flight_per_project",
        "ingest_spans_per_second",
        "ingest_spans_per_day",
        "ingest_bytes_per_day",
        "idempotency_pool_size",
        "idempotency_pool_timeout_seconds",
        "rate_limit_pool_size",
        "rate_limit_pool_timeout_seconds",
        "rate_limit_lock_timeout_ms",
        "rate_limit_statement_timeout_ms",
        "s3_force_path_style",
        "backups_enabled",
        "worker_required",
        "gateway_mode",
        "gateway_max_in_flight",
        "gateway_max_in_flight_bytes",
        "gateway_max_in_flight_per_key",
        "gateway_allow_insecure_base_urls",
        "gateway_record_concurrency",
        "gateway_record_backlog",
        "gateway_record_backlog_mb",
        "alerts_evaluation_enabled",
        "weekly_digest_enabled",
        "webhook_allow_private_targets",
        "alert_email_any_recipient",
        "pagerduty_events_url",
        "detectors_enabled",
        "provider_status_polling_enabled",
        "explain_model",
        "explain_monthly_budget_usd",
        "user_stats_enabled",
        "aggregate_repair_enabled",
        "evals_enabled",
        "eval_sampler_max_runs_per_cycle",
        "experiment_max_items",
        "experiment_schedules_enabled",
        "review_selection_enabled",
        "replay_monthly_budget_usd",
        "billing_sources_enabled",
        "audit_retention_days",
        "otel_service_name",
        "attribution_enabled",
        mode="before",
    )
    @classmethod
    def _blank_is_default(cls, value: object, info: ValidationInfo) -> object:
        # Same rule for settings that have a default instead of `None`: `docker compose` passes
        # `${SMTP_PORT:-}` through as an empty string, and that must not stop the app starting.
        if value == "" and info.field_name is not None:
            return cls.model_fields[info.field_name].default
        return value

    # pydantic applies validators in definition order, each wrapping the one before it, so the
    # "before" validator below runs first and the check above it receives its output.
    @field_validator("credentials_keys")
    @classmethod
    def _credentials_keys_are_well_formed(cls, value: SecretStr | None) -> SecretStr | None:
        # Fail at startup instead of on the first TOTP enrolment. parse_keyring's messages name
        # the entry and the broken rule but never quote the value.
        if value is not None:
            try:
                parse_keyring(value.get_secret_value())
            except ValueError as error:
                raise ValueError(f"CREDENTIALS_KEYS is invalid: {error}") from None
        return value

    @field_validator("credentials_keys", mode="before")
    @classmethod
    def _blank_credentials_keys_is_unset(cls, value: object) -> object:
        # Same rule as `_blank_is_unset`, plus: wrap the string in a SecretStr before the check
        # runs. pydantic records the input a validator received on its errors, and `errors()` and
        # `json()` print it in full, so the check must only ever see a masked value.
        if value == "":
            return None
        return SecretStr(value) if isinstance(value, str) else value

    @field_validator("ee_license_key")
    @classmethod
    def _ee_license_key_verifies(cls, value: SecretStr | None) -> SecretStr | None:
        # Fail at startup instead of on the first gated request. parse_license's messages name
        # the broken rule but never quote the key. Expiry is judged by each gate, not here.
        if value is not None:
            try:
                parse_license(value.get_secret_value())
            except ValueError as error:
                raise ValueError(f"EE_LICENSE_KEY is invalid: {error}") from None
        return value

    @field_validator("ee_license_key", mode="before")
    @classmethod
    def _blank_ee_license_key_is_unset(cls, value: object) -> object:
        # The same masking as `_blank_credentials_keys_is_unset`, for the same reason.
        if value == "":
            return None
        return SecretStr(value) if isinstance(value, str) else value

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip().rstrip("/") for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("app_base_url")
    @classmethod
    def _strip_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @field_validator("pagerduty_events_url")
    @classmethod
    def _pagerduty_url_is_https(cls, value: str) -> str:
        # Fail at startup instead of on the first alert: events carry the routing key, so they
        # only ever go over https to a named host.
        parts = urlsplit(value.strip())
        if parts.scheme != "https" or not parts.hostname or "@" in parts.netloc:
            raise ValueError("PAGERDUTY_EVENTS_URL must be an https:// URL with a host")
        return value.strip()

    @model_validator(mode="after")
    def _shutdown_deadline_order(self) -> Self:
        if (
            self.shutdown_work_seconds + self.shutdown_flush_seconds + self.shutdown_close_seconds
            >= self.shutdown_timeout_seconds
        ):
            raise ValueError("Shutdown must reserve time for producer and transaction joins")
        return self

    @model_validator(mode="after")
    def _rate_limit_deadline_order(self) -> Self:
        if self.rate_limit_lock_timeout_ms >= self.rate_limit_statement_timeout_ms:
            raise ValueError(
                "RATE_LIMIT_LOCK_TIMEOUT_MS must be less than RATE_LIMIT_STATEMENT_TIMEOUT_MS"
            )
        return self

    @model_validator(mode="after")
    def _require_a_real_secret_key(self) -> Self:
        # The development key is public and a short key can be brute-forced offline from one CSRF
        # token. Local development may keep either: nothing it signs leaves the machine.
        local = urlsplit(self.app_base_url).hostname in {"localhost", "127.0.0.1", "::1"}
        if (
            self.secure_cookies or not local
        ) and self.secret_key.get_secret_value() == DEV_SECRET_KEY:
            raise ValueError("SECRET_KEY must be set when APP_BASE_URL uses https or is not local")
        if not local and len(self.secret_key.get_secret_value()) < 32:
            raise ValueError(
                "SECRET_KEY must be at least 32 characters unless APP_BASE_URL is local"
            )
        return self

    @model_validator(mode="after")
    def _require_what_the_email_provider_needs(self) -> Self:
        required = {
            "resend": ("resend_api_key", "email_from"),
            "smtp": ("smtp_host", "email_from"),
        }.get(self.email_provider, ())
        missing = [name.upper() for name in required if getattr(self, name) is None]
        if missing:
            # Names only: this text ends up in startup logs, and the values are secrets.
            raise ValueError(f"EMAIL_PROVIDER={self.email_provider} requires {', '.join(missing)}")
        return self

    @model_validator(mode="after")
    def _oauth_providers_are_complete(self) -> Self:
        for name in OAUTH_PROVIDER_NAMES:
            client_id = getattr(self, f"oauth_{name}_client_id")
            client_secret = getattr(self, f"oauth_{name}_client_secret")
            if (client_id is None) != (client_secret is None):
                # A provider with half its settings would look configured and then fail on the
                # first sign-in, or silently stay off. Names only: the values are secrets.
                prefix = f"OAUTH_{name.upper()}"
                raise ValueError(
                    f"{prefix}_CLIENT_ID and {prefix}_CLIENT_SECRET must be set together"
                )
        return self

    @model_validator(mode="after")
    def _object_storage_is_complete(self) -> Self:
        required = {
            "S3_BUCKET": self.s3_bucket,
            "S3_ACCESS_KEY": self.s3_access_key,
            "S3_SECRET_KEY": self.s3_secret_key,
        }
        missing = [name for name, value in required.items() if value is None]
        if missing and len(missing) < len(required):
            # Half-set storage would look configured and then fail on the first backup, or
            # silently stay off. Names only: the values are secrets.
            raise ValueError(f"Object storage requires {', '.join(missing)} to be set as well")
        return self

    @model_validator(mode="after")
    def _backup_storage_is_complete(self) -> Self:
        required = {
            "BACKUP_S3_BUCKET": self.backup_s3_bucket,
            "BACKUP_S3_ACCESS_KEY": self.backup_s3_access_key,
            "BACKUP_S3_SECRET_KEY": self.backup_s3_secret_key,
        }
        missing = [name for name, value in required.items() if value is None]
        if missing and len(missing) < len(required):
            raise ValueError(f"Backup storage requires {', '.join(missing)} to be set as well")
        return self

    @property
    def is_backup_storage_configured(self) -> bool:
        """Whether explicit backup bucket and credentials are configured."""
        return (
            self.backup_s3_bucket is not None
            and self.backup_s3_access_key is not None
            and self.backup_s3_secret_key is not None
        )

    @property
    def is_demo_enabled(self) -> bool:
        """Demo traffic defaults to on exactly when an Anthropic key is configured."""
        if self.demo_enabled is None:
            return self.anthropic_api_key is not None
        return self.demo_enabled

    @property
    def is_api_docs_enabled(self) -> bool:
        return not self.secure_cookies if self.api_docs is None else self.api_docs

    @property
    def is_crypto_configured(self) -> bool:
        """Whether `core.crypto` can seal secrets: true exactly when CREDENTIALS_KEYS is set."""
        return self.credentials_keys is not None

    @property
    def is_replay_configured(self) -> bool:
        """Replays need a budget above zero and sealed credentials to call the provider with."""
        return self.replay_monthly_budget_usd > 0 and self.is_crypto_configured

    @property
    def is_email_configured(self) -> bool:
        """Whether email can actually reach someone.

        `resend` and `smtp` are only constructible with everything they need. `console` is
        configured only when it writes to a file the operator chose (development, e2e tests):
        logging an address and a subject delivers nothing, so features that depend on email
        stay off rather than pretend.
        """
        if self.email_provider == "console":
            return self.email_console_file is not None
        return True

    @property
    def is_object_storage_configured(self) -> bool:
        """Whether exports have somewhere to go: a bucket and both keys are set."""
        return (
            self.s3_bucket is not None
            and self.s3_access_key is not None
            and self.s3_secret_key is not None
        )

    def oauth_credentials(self, provider: str) -> tuple[str, SecretStr] | None:
        """The client id and secret registered for `provider`, or None if it is not configured."""
        if provider not in OAUTH_PROVIDER_NAMES:
            return None
        client_id: str | None = getattr(self, f"oauth_{provider}_client_id")
        client_secret: SecretStr | None = getattr(self, f"oauth_{provider}_client_secret")
        if client_id is None or client_secret is None:
            return None
        return client_id, client_secret

    @property
    def oauth_providers(self) -> tuple[OAuthProviderName, ...]:
        """The providers users can sign in with: those with both a client id and a secret."""
        return tuple(name for name in OAUTH_PROVIDER_NAMES if self.oauth_credentials(name))

    @property
    def secure_cookies(self) -> bool:
        return urlsplit(self.app_base_url).scheme == "https"

    @property
    def origin_allowlist(self) -> frozenset[str]:
        parts = urlsplit(self.app_base_url)
        base_origin = f"{parts.scheme}://{parts.netloc}"
        return frozenset([base_origin, *self.allowed_origins])


@lru_cache
def get_settings() -> Settings:
    return Settings()
