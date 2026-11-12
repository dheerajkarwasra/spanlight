# Spanlight — backend

FastAPI API, ingestion pipeline (native + OTLP), Postgres job worker and the `spanlight` admin CLI.
Why it is built this way: the [decision records](../docs/decisions/).
Contract clarifications: [`docs/api-deviations.md`](../docs/api-deviations.md).

## Run locally

Requires Python 3.13 (pinned in `.python-version` and the Dockerfile; `pyproject.toml` still accepts 3.12), [uv](https://docs.astral.sh/uv/) and Postgres 17.

```bash
cd backend
cp .env.example .env            # then edit DATABASE_URL / SECRET_KEY
uv sync
uv run spanlight migrate        # alembic upgrade head
uv run python -m app.server
uv run python -m app.jobs.worker   # in a second terminal: retention, cleanup, notification delivery, demo traffic
```

- API docs (OpenAPI): <http://localhost:8000/api/docs>
- Health: `/health/live`, `/health/ready`; Prometheus: `/metrics` (`Authorization: Bearer $METRICS_TOKEN`)

### Database role (important)

Row-level security on `traces`/`spans` is `ENABLE`d and `FORCE`d, which also applies to the table
owner — but **superusers and `BYPASSRLS` roles skip RLS entirely**. Run migrations and the app as an
ordinary role:

```sql
CREATE ROLE spanlight_app LOGIN PASSWORD '…' NOSUPERUSER NOBYPASSRLS;
CREATE DATABASE spanlight OWNER spanlight_app;  -- the database owner may create in `public` (PG 15+)
```

The `citext` extension is "trusted", so the owning role can create it during the migration.

## Environment

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://spanlight_app:spanlight_app@localhost:5432/spanlight` | psycopg 3 URL (async engine) |
| `APP_BASE_URL` | `http://localhost:8000` | Public origin; Origin allowlist, invite links, `Secure` cookies when https |
| `ALLOWED_ORIGINS` | – | Extra comma-separated origins (e.g. `http://localhost:5173`) |
| `SECRET_KEY` | dev default | HMAC key for CSRF tokens; **required** when `APP_BASE_URL` is https or its host is not local, and at least 32 characters unless its host is `localhost`, `127.0.0.1` or `[::1]` |
| `METRICS_TOKEN` | – | Bearer token for `/metrics`; unset disables it |
| `WORKER_METRICS_PORT` | – | Port on which the worker serves `/metrics` (same token); unset disables it |
| `CREDENTIALS_KEYS` | – | Master keys that encrypt stored secrets, `<id>:<base64 of 32 bytes>[,…]`; the first entry seals new data. Unset leaves encryption off, and with it two-factor authentication (`409 NOT_CONFIGURED`); a malformed value stops startup. See [ADR 9](../docs/decisions/0009-application-master-key-encryption.md) |
| `EE_LICENSE_KEY` | – | Enterprise license key (`spl_lic_…`); unset runs the community edition. A key that does not verify stops startup; an expired one turns its features off (`403 EE_LICENSE_REQUIRED`). `uv run spanlight license-status` shows what it grants. See [ADR 16](../docs/decisions/0016-open-core-ee-directories.md) |
| `ANTHROPIC_API_KEY` | – | Enables the real demo traffic job (`claude-haiku-4-5`) |
| `DEMO_MONTHLY_BUDGET_USD` | `1.00` | Demo stops once recorded month-to-date spend reaches this |
| `DEMO_ENABLED` | key present | Demo job + `POST /api/v1/demo/session` |
| `EMAIL_PROVIDER` | `console` | How mail is sent: `resend`, `smtp` or `console`. A provider missing what it needs stops startup |
| `EMAIL_FROM` | – | Sender address; required for `resend` and `smtp` |
| `RESEND_API_KEY` | – | Required for `resend` |
| `SMTP_HOST` / `SMTP_PORT` | – / `587` | Mail server for `smtp` (`SMTP_HOST` is required) |
| `SMTP_USERNAME` / `SMTP_PASSWORD` | – | Log in to the mail server when a username is set |
| `SMTP_STARTTLS` | `true` | Upgrade the connection with STARTTLS (port 587; implicit TLS on 465 is not supported); turn off only for a trusted local relay |
| `EMAIL_CONSOLE_FILE` | – | `console` only: append every message to this file as a JSON line. Email counts as configured for `console` only when this is set |
| `OAUTH_GITHUB_CLIENT_ID` / `OAUTH_GITHUB_CLIENT_SECRET` | – | Sign in with GitHub. On when both are set; setting only one stops startup. Register `<APP_BASE_URL>/api/v1/auth/oauth/github/callback` as the callback URL |
| `OAUTH_GOOGLE_CLIENT_ID` / `OAUTH_GOOGLE_CLIENT_SECRET` | – | Sign in with Google, the same way. Callback URL `<APP_BASE_URL>/api/v1/auth/oauth/google/callback` |
| `API_POOL_TIMEOUT_SECONDS` / `API_STATEMENT_TIMEOUT_SECONDS` | `1` / `10` | Seconds an API request waits for a database connection, and the longest one of its statements may run, before it answers `503` with `Retry-After` |
| `RATE_LIMIT_LOCK_TIMEOUT_MS` / `RATE_LIMIT_STATEMENT_TIMEOUT_MS` | `100` / `250` | Dedicated token-bucket SQL lock/statement deadlines in milliseconds; require `0 < lock < statement`. SQL errors fail open and increment unavailable metrics. Checkout (`RATE_LIMIT_POOL_TIMEOUT_SECONDS`, `0.25` s) and request/cancellation cleanup wall time remain separate. Authentication throttles and budgets keep their existing policies. |
| `IDEMPOTENCY_POOL_SIZE` / `IDEMPOTENCY_POOL_TIMEOUT_SECONDS` | `5` / `5` | Connections the API keeps for idempotency keys, in a pool apart from the main one, and the seconds a request waits for one before failing |
| `SENTRY_DSN` | – | Optional error reporting (api and worker) |
| `LOG_LEVEL` / `LOG_JSON` | `INFO` / `true` | Structured JSON logs on stdout |

Behind a reverse proxy, run uvicorn with `--proxy-headers --forwarded-allow-ips=<proxy>` so client
IPs (login throttling, audit log) are real. Rate limits and idempotency keys live in Postgres, so
several API replicas share them.

## Admin CLI

```bash
uv run spanlight create-user --email ada@example.com --name "Ada" --org acme --role owner
uv run spanlight reset-password --email ada@example.com   # also revokes all sessions
uv run spanlight reset-2fa --email ada@example.com        # lost app and recovery codes
uv run spanlight sync-prices                              # insert the bundled price snapshot
```

`create-user` and `reset-password` prompt for the password. All three user commands write audit
events for the user's orgs. `reset-2fa` turns two-factor authentication off and deletes the recovery
codes; it leaves the password and the sessions alone.

## Tests

Tests use real Postgres 17 through Testcontainers. Each worker owns its container and a UUID-named
child database, removed at session teardown. The suite creates an unprivileged `spanlight_app_test`
role, migrates as that role, and connects the app through it so RLS is enforced.

Configure the Docker endpoint before running tests. For Docker Cloud or another remote daemon,
provide its supported `DOCKER_HOST`/TLS settings and `TESTCONTAINERS_HOST_OVERRIDE` when the
published-port host differs. No Docker endpoint is started automatically by pytest.

An explicitly supplied `TEST_DATABASE_URL` must use a superuser on a disposable database named
`spanlight_test` or `spanlight_test_*`. The suite creates UUID child databases beside it; it never
resets the supplied database. Ordinary development names and URL query/fragment overrides fail
before any connection or schema reset. The admin account must be allowed to create databases and
roles. Do not supply a production server even with a test-named database.

```bash
cd backend
uv sync --frozen
uv run pytest -n auto -m "not serial"                    # the suite, one worker per CPU
uv run pytest -m serial                                   # timing and capacity bounds, alone
uv run pytest tests/ingest/test_ingest.py -k idempotent   # a few tests need no workers
uv run ruff check && uv run ruff format --check
uv run mypy app
```

Tests marked `serial` assert wall-clock bounds (deadlines, capacity, process shutdown). Run in
parallel, they compete with other workers for the same CPUs and fail on timing alone, so they
get their own pass without workers.

The demo-traffic tests use the real `anthropic` SDK on a mock HTTP transport; nothing calls the
network or spends money.

## Layout

```
app/
  main.py          create_app(): middleware, error handlers, routers; create_gateway_app()
  server.py        the launcher the image runs (`python -m app.server`)
  config.py        Settings (pydantic-settings)
  api/             health, ingest (`/v1/traces`, `/v1/otlp/traces`) and gateway (`/gw/v1`) routers, deps
  api/v1/          the dashboard API, one thin router per domain
  core/            security, crypto, permissions, errors (problem+json), ratelimit, logging, redact, middleware
  db/              models, async engine/session, RLS helpers, migration helpers
  ingest/          schemas (SpanIn), OTLP mapping, normalize, pipeline, store
  pricing/         versioned prices + cost calculation
  gateway/         the LLM gateway: keys, routes, cache, budgets guard, adapters, span recorder
  insights/        the Doctor: detectors (pure functions), engine, jobs
  jobs/            queue (lease/fence), worker loop, scheduler, tasks
  notifications/   transactional outbox, deliverer registry, delivery job
  email/           message, sender interface, console/Resend/SMTP providers, outbox deliverer
  <domain>/        one package per area: alerts, budgets, rollups, metrics, explorer, attribution,
                   reconciliation, prompts, datasets, evals, scores, review, playground, exports,
                   backups, provider_status, releases, end_users, live, auth, …
  services/        sessions, audit, demo workspace, slugs
  ee/              license-gated features (own license)
  cli.py           `spanlight`
alembic/           hand-written migrations (enums, citext, RLS policies, notification outbox)
tests/
```


### Process shutdown

Run the API with `python -m app.server`, the standalone gateway with `python -m app.gateway`,
and the worker with `python -m app.jobs.worker`. The shared HTTP launcher preserves proxy headers,
API port8000 and gateway port8001 (override `PORT`; gateway also accepts `GATEWAY_HOST`).
Serving processes remove `MIGRATION_DATABASE_URL` before loading application settings.
Uvicorn is pinned to0.54.0: upgrading requires revalidating startup ownership, signal semantics,
request/finalizer joins and the child-process matrix.

`SHUTDOWN_TIMEOUT_SECONDS=60`, `SHUTDOWN_WORK_SECONDS=50`, `SHUTDOWN_FLUSH_SECONDS=3`, and
`SHUTDOWN_CLOSE_SECONDS=2` use one monotonic first-signal deadline. Defaults allocate work through50s,
producer joins through52.5s, settlement/recorder writes through53.75s, cancellation joins through55s,
loss flush through58s, and resource close through60s. Earlier completion leaves time available.
Values must be finite and positive, with work plus both reserves strictly below the total.
Blank values use defaults. Repeated SIGTERM never resets the clock; repeated SIGINT explicitly forces
termination without unsafe resource unwinding. Direct lifespan embedding retains the resource safety gate and teardown deadline, but its host
must stop ingress and join all request producers before exiting the lifespan. App lifespan alone
cannot establish that request barrier. Use the shared adapter for production HTTP serving.

Compose requests75s stop grace for all backend roles. Railway requests75s `drainingSeconds` for the
API and worker; apply the same request to a separately configured gateway. Installed railway3.13.0
supports the field, but hosted enforcement and signal-delivery overhead remain unverified.
Do not shorten external grace below the configured application total. A noncooperative database user
can exceed the deadline: its pool is retained, shutdown is reported incomplete, and external SIGKILL
may lose in-memory accounting. Joined uncertain terminal writes also remain incomplete.
A blocked tracing exporter is bounded daemon work, never evidence of completed exporter cleanup.

The production entrypoints also retain a public `shutdown_default_executor()` join before returning
to the event-loop runner. This covers default-executor resolver work hidden inside HTTP libraries.
If the join crosses the original deadline, shutdown remains incomplete and the process stays owned
until completion or external hard stop. Embedded hosts keep their shared executor; they own its final
join. Explicit SMTP, file, heuristic, S3, password-hash and system-resolver awaits retain thread work
through cancellation. A cancelled S3 acquisition closes a returned response body or aborts a known
multipart upload; unacknowledged external effects retain their existing uncertainty.

For development-only reload, `uv run uvicorn app.main:app --reload --port 8000` remains available;
it bypasses the supported process shutdown adapter and does not provide its request/executor barrier.
