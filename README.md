# Spanlight

[![CI](https://github.com/dheerajkarwasra/spanlight/actions/workflows/ci.yml/badge.svg)](https://github.com/dheerajkarwasra/spanlight/actions/workflows/ci.yml) [![CodeQL](https://github.com/dheerajkarwasra/spanlight/actions/workflows/codeql.yml/badge.svg)](https://github.com/dheerajkarwasra/spanlight/actions/workflows/codeql.yml) [![PyPI](https://img.shields.io/pypi/v/spanlight.svg)](https://pypi.org/project/spanlight/) [![License: MIT core](https://img.shields.io/badge/license-MIT%20core-blue.svg)](#license)

**Open-source observability for LLM applications.** Add three lines to your Python app, or change one `base_url`, and see each LLM call: prompts and completions, latency, time to first token, token usage, cost and errors, grouped into traces and user sessions.

- **Real traffic only.** Instrument your app with the Python SDK, point any OpenTelemetry GenAI exporter at the OTLP endpoint, or send calls through the gateway.
- **A gateway for OpenAI and Anthropic.** It is built so the official SDKs work against it unchanged, streaming and tool calls included; a contract suite against recorded provider responses is planned for the 0.3 release. It adds encrypted provider credentials, weighted routes with retries and fallbacks, per-key rate limits, an exact-match response cache, and an Integration Lab that injects the failures real providers produce (429s, 5xx, timeouts, cut-off streams) on non-production keys.
- **Cost you can check.** Prices are versioned per model. A model with no known price shows as _unknown_, never as $0. Cache-write premiums and long-context price tiers are not modelled yet.
- **Built for teams.** Organizations, projects, roles, invites, scoped API keys, personal access tokens and an audit log.
- **Private by design.** Postgres row-level security isolates every project's traces, spans and derived data; the rest is guarded by permission checks. Secrets are redacted from payloads before storage, and payload capture can be switched off per project.
- **Self-hosted on one database.** Postgres is the only datastore, the job queue included. One `docker compose up`, or [deploy to Railway](docs/deploy/railway.md).

**Status:** pre-1.0. Tracing, the dashboard, sessions, teams and the Python SDK work end to end. The 0.2 hardening work (email verification and password reset, GitHub and Google sign-in, two-factor authentication, token scopes, exports, backups, hourly rollups) is built into the API and the dashboard, and is unreleased. The 0.3 gateway and Integration Lab, the 0.4 alerts and budgets, and the 0.5 Doctor, releases and users are built and unreleased. See the [changelog](CHANGELOG.md).

## Quickstart

### 1. Start the platform

```bash
cp deploy/.env.example deploy/.env    # fill in the secrets
docker compose -f deploy/compose.yaml --env-file deploy/.env up -d --build
```

Open http://localhost:8080, create an account, and follow the setup wizard to create a project and an API key.

### 2. Instrument your app

Install the SDK from PyPI (Python 3.10 or newer):

```bash
pip install "spanlight[anthropic]"
```

Use the `openai` extra for OpenAI.

```python
import spanlight
from anthropic import Anthropic

spanlight.init(api_key="spl_live_…", host="http://localhost:8080")
client = spanlight.wrap_anthropic(Anthropic())

@spanlight.observe()
def answer(question: str) -> str:
    reply = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=300,
        messages=[{"role": "user", "content": question}],
    )
    return reply.content[0].text
```

Or skip the SDK: add a provider credential and a gateway key in the dashboard (Gateway), then point your existing client at Spanlight:

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:8080/gw/v1", api_key="spl_gw_…")
```

For Anthropic use `Anthropic(base_url="http://localhost:8080/gw", api_key="spl_gw_…")`. See the gateway quickstart on the docs site.

### 3. Watch it arrive

The wizard shows your first trace live as it arrives. From there you have an overview dashboard, a searchable trace explorer with a span waterfall, and session views of multi-turn conversations.

## Architecture

```
 your app ──SDK / OTLP──▶ web (Caddy) ──▶ api (FastAPI) ──▶ Postgres ◀── worker
          ──/gw/v1──────▶  SPA, /docs      auth, ingest,     traces, spans,   rollups, email,
                                           queries, gateway  rollups, jobs    exports, backups,
                                               │                              retention
                                               └──▶ OpenAI / Anthropic / OpenAI-compatible
```

One Docker image runs all three roles. Ingestion is synchronous and idempotent by design: a replayed batch upserts the same rows and totals are recomputed, so SDK retries do not double-count. The gateway runs inside the api by default and can run as its own service with `GATEWAY_MODE=standalone`; each gateway call is recorded as one span through the same ingestion path, written after the answer is sent; a failed write is retried, and a span still lost is counted in `spanlight_gateway_record_failures_total` and in `gateway_spans_lost` on `/health/ready`. An S3-compatible store is optional and holds exports and nightly backups.

| Path                           | What it is                                                                                         |
| ------------------------------ | -------------------------------------------------------------------------------------------------- |
| [`backend/`](backend/)         | FastAPI, SQLAlchemy 2 (async), Alembic, a Postgres-backed job queue, and the `spanlight` admin CLI |
| [`frontend/`](frontend/)       | React 19, TypeScript, TanStack Router and Query, Tailwind v4, Radix and Recharts                   |
| [`sdks/python/`](sdks/python/) | `spanlight` SDK: `@observe`, spans, and OpenAI and Anthropic wrappers                              |
| [`deploy/`](deploy/)           | Dockerfile, Caddyfile, Compose files, example Prometheus config and Grafana dashboard              |
| [`docs-site/`](docs-site/)     | The documentation site served at `/docs/`                                                          |
| [`docs/`](docs/)               | Decision records, runbooks, security documents, API notes and the Railway guide                    |

### Design decisions

- [Postgres is the only datastore](docs/decisions/0001-postgres-only-storage.md), which keeps operations simple, with a stated path to scale.
- [Server-side sessions, hashed API keys](docs/decisions/0002-sessions-not-jwt.md): revocation takes effect immediately.
- [Row-level security for tenant isolation](docs/decisions/0003-row-level-security.md): a forgotten filter fails closed.
- [Synchronous, idempotent ingestion](docs/decisions/0004-synchronous-idempotent-ingestion.md): a replayed batch upserts the same rows, so SDK retries do not double-count.
- [Versioned API paths](docs/decisions/0005-public-api-under-api-v1.md): the dashboard API lives under `/api/v1`.
- [Hourly rollups with bucketed histograms](docs/decisions/0006-hourly-rollups-with-bucketed-histograms.md): long-window charts stay fast and still report percentiles.
- [Postgres-backed rate limiting](docs/decisions/0007-postgres-backed-rate-limiting.md): one limit shared by every API replica.
- [Gateway embedded by default](docs/decisions/0008-gateway-embedded-by-default.md): one process for small installs, a standalone gateway by flag.
- [Encryption with an application master key](docs/decisions/0009-application-master-key-encryption.md): stored secrets such as two-factor seeds and provider API keys are sealed at rest.
- [Exact-match gateway cache](docs/decisions/0010-exact-match-gateway-cache.md): identical non-streaming requests are answered from Postgres, opt-in per key.
- [Transactional outbox](docs/decisions/0011-transactional-outbox.md): email and other notifications are sent once, after the change that caused them commits.
- [Budget blocking reads the evaluated state](docs/decisions/0012-budget-block-reads-evaluated-state.md): the gateway refuses calls while a blocking budget's alert rule is firing, with no per-call spend sums.

## Documentation

- **Docs site:** every instance serves it at `/docs/` (for example http://localhost:8080/docs/): quickstart, Python SDK, OTLP, the gateway (quickstart, routing, cache, Integration Lab, credentials, errors), self-hosting, configuration, the API reference, security and runbooks.
- [Python SDK](sdks/python/README.md): install, the `@observe` and `span` API, the OpenAI and Anthropic wrappers, and delivery guarantees.
- [API notes](docs/api-deviations.md): error format, auth and CSRF, ingestion, OTLP and metric definitions as implemented.
- [Runbooks](docs/runbooks/) and [security](docs/security/): operating, restoring and securing an instance.
- [Contributing](CONTRIBUTING.md), the [security policy](SECURITY.md) and the [changelog](CHANGELOG.md).

## Development

| Component  | Run                                                        | Check                                                                                                            |
| ---------- | ---------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| Backend    | `cd backend && uv run uvicorn app.main:app --reload`       | `uv run ruff check && uv run ruff format --check && uv run mypy app && uv run pytest`                            |
| Frontend   | `cd frontend && npm run dev` (proxies `/api` to `:8000`)   | `npm run lint && npm run format:check && npm run typecheck && npm test`                                          |
| SDK        | —                                                          | `cd sdks/python && uv run ruff check && uv run ruff format --check && uv run mypy src && uv run pytest`          |
| Docs site  | `cd docs-site && npm run dev` (serves `/docs/` on `:4321`) | `npm run build` (fails on a broken link), then `cd backend && uv run python scripts/gen_config_table.py --check` |
| End-to-end | full stack running                                         | `cd frontend && E2E_BASE_URL=http://localhost:5173 npx playwright test`                                          |

Backend tests need a real Postgres (`TEST_DATABASE_URL`); see [backend/README.md](backend/README.md). [CONTRIBUTING.md](CONTRIBUTING.md) covers setup, the test database, commit conventions and the review checklist.

## What's next

Prompts, datasets and evals are in development and unreleased. After them: a JavaScript SDK, per-project detector thresholds and semantic caching. Open an issue to discuss priorities.

## License

Spanlight is open core. Everything is [MIT](LICENSE) except code under directories named `ee`, which is under the [Spanlight Enterprise License](backend/app/ee/LICENSE) and runs only with a license key. Those directories hold no features yet; the community edition is the complete product and needs no key. The SDK is MIT. See [ADR 16](docs/decisions/0016-open-core-ee-directories.md).
