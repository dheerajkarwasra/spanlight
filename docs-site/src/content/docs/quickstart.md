---
title: Quickstart
description: Start Spanlight with Docker Compose, instrument a Python app and see your first trace.
sidebar:
  order: 1
---

This takes about ten minutes and ends with a trace from your own code in the dashboard. You need Docker with the Compose plugin and Python 3.10 or newer.

## 1. Start the platform

```bash
git clone https://github.com/dheerajkarwasra/spanlight.git
cd spanlight
cp deploy/.env.example deploy/.env
```

Open `deploy/.env` and fill in the three secrets it asks for (`POSTGRES_PASSWORD`, `APP_DB_PASSWORD` and `SECRET_KEY`). The file explains how to generate them:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Then build and start the stack:

```bash
docker compose -f deploy/compose.yaml --env-file deploy/.env up -d --build
```

When it is ready, `http://localhost:8080/health/ready` answers `200` with `"status":"ok"` (and `"database":"ok"`, `"migrations":"ok"`, plus the worker's heartbeat age and the notification backlog). Open `http://localhost:8080`, create an account, and follow the setup wizard to create a project and an API key. The key starts with `spl_live_` and is shown once, so copy it now.

## 2. Instrument your app

```bash
pip install "spanlight[anthropic]"
```

Use `spanlight[openai]` for OpenAI. Then:

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

print(answer("How long do refunds take?"))
spanlight.flush()  # short scripts only: sends what is still queued before exit
```

Instead of passing them to `init`, you can set `SPANLIGHT_API_KEY` and `SPANLIGHT_HOST` in the environment.

## 3. Watch it arrive

The wizard shows the first trace live. From there you have an overview of latency, tokens, cost and errors, a trace explorer with a span waterfall, and session views of multi-turn conversations.

A model with no known price shows its cost as unknown, never as $0.

## Next steps

- [Python SDK](/docs/python-sdk/): every option, `span`, `update_trace`, streaming and delivery guarantees.
- [OTLP](/docs/otlp/): send traces from OpenTelemetry instead of the SDK.
- [Self-hosting](/docs/self-hosting/): what runs, how to expose it and how to keep it healthy.
- [Configuration](/docs/configuration/): turn on email, sign in with GitHub or Google, backups and exports.
