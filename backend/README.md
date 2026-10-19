# Spanlight — backend

FastAPI API, ingestion pipeline (native + OTLP), Postgres job worker and the `spanlight` admin CLI.
Why it is built this way: the [decision records](../docs/decisions/).
Contract clarifications: [`docs/api-deviations.md`](../docs/api-deviations.md).

## Run locally

Requires Python 3.12+, [uv](https://docs.astral.sh/uv/) and Postgres 17.

```bash
cd backend
cp .env.example .env            # then edit DATABASE_URL / SECRET_KEY
uv sync
uv run spanlight migrate        # alembic upgrade head
uv run uvicorn app.main:app --reload --port 8000
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
| `SECRET_KEY` | dev default | HMAC key for CSRF tokens; **required** when `APP_BASE_URL` is https |
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
| `API_POOL_TIMEOUT_SECONDS` / `API_STATEMENT_TIMEOUT_SECONDS` | `5` / `10` | Seconds an API request waits for a database connection, and the longest one of its statements may run, before it answers `503` with `Retry-After` |
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

Tests run against a real Postgres. The suite rebuilds the `public` schema, creates an unprivileged
`spanlight_app_test` role, migrates as that role and connects the app as it (so RLS is really
enforced). `TEST_DATABASE_URL` must name a superuser, which creates that role and empties the
tables between tests. With `-n auto` (pytest-xdist) each worker builds its own database next to
the configured one (`spanlight_test_gw0`, `spanlight_test_gw1`, ...).

```bash
docker run -d --name spanlight-test-pg -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=spanlight_test \
  -p 55432:5432 postgres:17
export TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:55432/spanlight_test  # default

uv run pytest -n auto                                     # the suite, one worker per CPU
uv run pytest tests/ingest/test_ingest.py -k idempotent   # a few tests need no workers
uv run ruff check && uv run ruff format --check
uv run mypy app
```

The demo-traffic tests use the real `anthropic` SDK on a mock HTTP transport; nothing calls the
network or spends money.

## Layout

```
app/
  main.py          create_app(): middleware, error handlers, routers
  config.py        Settings (pydantic-settings)
  api/             routers: auth, orgs, projects, keys, traces, metrics, ingest, health, demo
  core/            security, crypto, permissions, errors (problem+json), ratelimit, logging, redact, middleware
  db/              models, async engine/session, RLS helpers, migration helpers
  ingest/          schemas (SpanIn), otlp mapping, normalize, pipeline
  pricing/         seed prices + cost calculation
  jobs/            queue (lease/fence), worker loop, scheduler, tasks
  notifications/   transactional outbox, deliverer registry, delivery job
  email/           message, sender interface, console/Resend/SMTP providers, outbox deliverer
  services/        sessions, audit, demo workspace, slugs
  cli.py           `spanlight`
alembic/           hand-written migrations (enums, citext, RLS policies, notification outbox)
tests/
```
