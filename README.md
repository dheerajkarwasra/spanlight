<div align="center">

<img src="frontend/public/favicon.svg" alt="Spanlight logo" width="72" height="72">

# Spanlight

**Open-source observability for LLM applications, on one Postgres.**

[![CI](https://github.com/dheerajkarwasra/spanlight/actions/workflows/ci.yml/badge.svg)](https://github.com/dheerajkarwasra/spanlight/actions/workflows/ci.yml)
[![CodeQL](https://github.com/dheerajkarwasra/spanlight/actions/workflows/codeql.yml/badge.svg)](https://github.com/dheerajkarwasra/spanlight/actions/workflows/codeql.yml)
[![PyPI](https://img.shields.io/pypi/v/spanlight.svg)](https://pypi.org/project/spanlight/)
[![License: MIT core](https://img.shields.io/badge/license-MIT%20core-blue.svg)](LICENSE)

[Live demo](https://spanlight.dev) · [Docs](https://spanlight.dev/docs/) · [Features](#features) · [Getting started](#getting-started) · [How it works](#how-it-works)

</div>

Spanlight records every LLM call your application makes: prompts and completions, latency, time to first token, tokens, cost and errors, grouped into traces and user sessions. Send traces with the Python SDK, any OpenTelemetry exporter, or by changing one `base_url` to the built-in gateway. Then go past tracing: audit what each call cost, check it against the provider's bill, find out why calls fail, and review yesterday's traffic every morning.

> [!TIP]
> Want to look before you install? The [live demo](https://spanlight.dev) runs on real Claude calls under a small monthly budget.

## Features

- **Trace explorer and sessions.** Search traces, read a span waterfall, and replay multi-turn conversations as chat with cost and latency per turn.
- **Cost you can audit.** Prices come from a versioned table that covers cache writes, long-prompt tiers, batch, flex and priority rates, and web search fees. Every call has a line-by-line cost breakdown. A cost that cannot be known shows as _unknown_ with the reason, never as $0.
- **Checked against the bill.** Connect an Anthropic or OpenAI admin key, or upload a CSV, and Spanlight compares traced spend with what the provider billed, every day.
- **Attribution and budgets.** Tag traces with up to 32 metadata pairs (customer, feature, team) and see spend, calls and errors per value. Budgets can alert on, or block, spend per project, gateway key, user, model, metadata pair or trace name.
- **The Doctor and provider health.** Detectors explain what is going wrong, and provider health tells a provider outage or rate limit apart from your own bug, with the provider's status page incidents alongside.
- **A gateway for OpenAI and Anthropic.** The official SDKs work against it unchanged, streaming and tool calls included. It adds sealed provider credentials, routes with retries and fallbacks, per-key rate limits, an exact-match cache, and an Integration Lab that injects real provider failures on non-production keys.
- **Prompts, evals and a daily review.** Versioned prompts with labels, a playground, exact replay of any recorded call, datasets, experiments with 95% confidence intervals on pass rates, and a nightly queue of the traces worth reading.
- **OpenTelemetry native.** `POST /v1/otlp/traces` reads the GenAI conventions (`gen_ai.*`, also written by LiteLLM), OpenInference, OpenLLMetry (Traceloop) and the Vercel AI SDK, and counts usage once across nested spans.
- **Built for teams, private by design.** Organizations, projects, roles, scoped API keys and an audit log. Postgres row-level security isolates every project's data, and secrets are redacted from payloads before storage.
- **Self-hosted on one database.** Postgres is the only datastore, the job queue included. The core is MIT licensed.

> [!NOTE]
> Spanlight is pre-1.0. The current server release is 0.6.0 ([changelog](CHANGELOG.md)), and the Python SDK on PyPI is 0.2.0.

## Getting started

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/) with Compose v2
- Python 3.10 or newer for the SDK
- An OpenAI or Anthropic API key in your own application

### 1. Start Spanlight

```bash
git clone https://github.com/dheerajkarwasra/spanlight.git
cd spanlight
cp deploy/.env.example deploy/.env    # fill in POSTGRES_PASSWORD, APP_DB_PASSWORD and SECRET_KEY
docker compose -f deploy/compose.yaml --env-file deploy/.env up -d --build
```

Open http://localhost:8080, create an account, and follow the setup wizard to create a project and an API key.

> [!TIP]
> Generate each secret with `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`. To host it instead, see [deploy to Railway](docs/deploy/railway.md).

### 2. Send your first trace

Install the SDK with the extra for your provider (`anthropic` or `openai`):

```bash
pip install "spanlight[anthropic]"
```

```python
import spanlight
from anthropic import Anthropic

spanlight.init(api_key="spl_live_…", host="http://localhost:8080")
client = spanlight.wrap_anthropic(Anthropic())

@spanlight.observe()
def answer(question: str) -> str:
    reply = client.messages.create(
        model="claude-haiku-5-5",
        max_tokens=300,
        messages=[{"role": "user", "content": question}],
    )
    return reply.content[0].text
```

<details>
<summary>No SDK: use the gateway</summary>

Add a provider credential and a gateway key in the dashboard (**Gateway**), then point your existing client at Spanlight:

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:8080/gw/v1", api_key="spl_gw_…")
```

For Anthropic, use `Anthropic(base_url="http://localhost:8080/gw", api_key="spl_gw_…")`.

</details>

<details>
<summary>No SDK: use OpenTelemetry</summary>

Point any OTLP/HTTP exporter at `http://localhost:8080/v1/otlp/traces` with the header `Authorization: Bearer spl_live_…`. JSON and protobuf are both accepted. See the [OTLP guide](https://spanlight.dev/docs/otlp/) for the attributes each library sends.

</details>

### 3. Watch it arrive

The wizard shows your first trace live. From there you have the overview dashboard, the trace explorer, sessions, cost, attribution and the Doctor.

## How it works

```
 your app ──SDK / OTLP──▶ web (Caddy) ──▶ api (FastAPI) ──▶ Postgres ◀── worker
          ──/gw/v1──────▶  SPA, /docs      auth, ingest,     traces, spans,   rollups, reconciliation,
                                           queries, gateway  rollups, jobs    review, email, retention
                                               │
                                               └──▶ OpenAI / Anthropic / OpenAI-compatible
```

One Docker image runs three roles: `api`, `worker` and `web`. Ingestion is synchronous and idempotent, so a replayed batch upserts the same rows and SDK retries never double-count. The gateway runs inside the api by default and can run on its own with `GATEWAY_MODE=standalone`. The reasons behind each choice are in the [decision records](docs/decisions/).

| Path                           | What it is                                                                    |
| ------------------------------ | ----------------------------------------------------------------------------- |
| [`backend/`](backend/)         | FastAPI, SQLAlchemy 2 (async), Alembic, the Postgres job queue and the CLI    |
| [`frontend/`](frontend/)       | React 19, TypeScript, TanStack Router and Query, Tailwind v4, Radix, Recharts |
| [`sdks/python/`](sdks/python/) | The `spanlight` SDK: `@observe`, spans, and the OpenAI and Anthropic wrappers |
| [`deploy/`](deploy/)           | Dockerfile, Caddyfile, Compose file, Prometheus example and Grafana dashboard |
| [`docs-site/`](docs-site/)     | The documentation site, served by every instance at `/docs/`                  |
| [`docs/`](docs/)               | Decision records, runbooks, security documents and API notes                  |

## Resources

- [Documentation](https://spanlight.dev/docs/): quickstart, SDK, OTLP, gateway, cost, self-hosting, configuration and the API reference. Every instance also serves it at `/docs/`.
- [Python SDK](sdks/python/README.md): the `@observe` and `span` API, the wrappers and delivery guarantees.
- [API notes](docs/api-deviations.md): error codes, ingestion rules and metric definitions as implemented.
- [Runbooks](docs/runbooks/) and [security](docs/security/): operating, restoring and securing an instance.
- [Backend](backend/README.md) and [contributor](CONTRIBUTING.md) guides: local setup, the test database and the checks CI runs.

Found a bug or have an idea? [Open an issue](https://github.com/dheerajkarwasra/spanlight/issues).
