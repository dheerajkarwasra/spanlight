# OTLP fixture recorder harness

Tooling that records the OTLP compatibility fixtures under `sdks/fixtures/otlp/`. Nothing in
this directory ships in a package: it is its own `uv` project (`pyproject.toml`), separate from
the SDK and the backend, and `pytest` never imports it from anywhere else.

## Layout

| Path | Role |
| --- | --- |
| `fake_provider.py` | Fake OpenAI and Anthropic API that replays the bodies under `responses/` |
| `capture.py` | OTLP endpoint that stores every export (`POST /v1/otlp/traces`) verbatim and answers `{}` |
| `_server.py` | The `http.server` plumbing both share: bind to `127.0.0.1` only, bounded bodies (5 MiB, 413 above), serve in a thread, print the port |
| `capture_bodies.py` | Owner-run script that records the real provider bodies (see "Owner action") |
| `responses/<provider>/<scenario>.json` or `.sse` | The bodies the fake provider serves |
| `tests/` | `uv run pytest` |

Runtime dependencies of the two servers: the standard library only. `httpx` and
`opentelemetry-proto` are for the recorder and the tests.

## Running the servers

From Python:

```python
from fake_provider import serve as serve_provider
from capture import serve as serve_capture

provider = serve_provider()              # .url, .port, .close()
capture = serve_capture()                # .url, .exports: list[(content_type, body)], .close()
```

From a shell (each prints its bound port on the first line of stdout, then serves until
interrupted; run them by file path, the directory is not a package):

```bash
uv run python fake_provider.py [--responses DIR] [--port 0]
uv run python capture.py [--out DIR] [--port 0]
```

`capture.py --out DIR` also writes each export as `0001.pb` / `0001.json` (by content type) so a
recorder in another process (the Node recipe) can hand the bytes to `record.py --from-capture`.

Point a client at the fake provider with its `base_url` and the key `sk-fixture` (any key is
accepted), and an exporter at the capture server with
`OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=<capture url>/v1/otlp/traces` and
`OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf`.

## How the fake provider picks a body

| Request | Provider directory | Default scenario |
| --- | --- | --- |
| `POST /v1/chat/completions` | `openai` | `chat` |
| `POST /v1/responses` | `openai` | `responses` |
| `POST /v1/embeddings` | `openai` | `embeddings` |
| `POST /v1/messages` | `anthropic` | `messages` |

- The `x-fixture-scenario` request header overrides the default scenario.
- A JSON body with `"stream": true` gets `<scenario>.sse` as `text/event-stream`; otherwise
  `<scenario>.json` as `application/json`.
- A scenario named `error_<status>` answers with that HTTP status and its `.json` body;
  `error_429` adds `retry-after: 1`.
- A scenario whose file is absent answers 404 with a JSON error naming the missing file. The
  harness never invents a body, so a recipe cannot record a scenario the owner has not captured.

## Recording and `--check`

```bash
uv run python record.py --library otel-genai-python --version 2.4b0            # writes ../otel-genai-python/2.4b0.json
uv run python record.py --library otel-genai-python --version latest --check   # the weekly job's mode
uv run python record.py --from-capture DIR --library vercel-ai-genai --version 7.0.142
```

`record.py` starts both servers, installs the recipe's packages into a `uv` virtual environment
in a temporary directory (never a project venv), runs each scenario as a subprocess against the
fake provider, decodes what the capture server received (`preview`), asks the backend's mapper
what it stores for the bytes (`uv run --project ../../../../backend python
scripts/otlp_expected.py`, the `expected` section) and writes `<out>/<library>/<version>.json`
(`--out` defaults to `sdks/fixtures/otlp`). `--version latest` leaves the library unpinned and
names the file after the version that was installed. `--check` writes the file too
(the weekly job uploads it and tests it), then diffs
`expected` field by field against the committed fixture (`--fixtures-root`, default
`sdks/fixtures/otlp`; with `latest`, the highest version on disk), prints one
`scenarios[i]....: old != new` line per difference and exits 1 on any. The only network use at
record time is installing the packages; the fixture tests make no network call and need no key.

A Python recipe is one module `python/recipes/<library>.py` (hyphens become underscores):

| Name | Meaning |
| --- | --- |
| `PACKAGE` | The package `--version` pins (`opentelemetry-instrumentation-openai-v2`) |
| `PACKAGES` | Every other package, pinned to an exact version (`{"openai": "2.54.0"}`); `opentelemetry-sdk` and `-exporter-otlp-proto-http` 1.45.1 are added by the recorder |
| `SCENARIOS` | Scenario names, in fixture order |
| `LOSES` | Scenario name to the native fields the exporter does not send (the compatibility page prints them) |
| `run(scenario, provider_url)` | Runs one scenario: `_otel.configure(scenario)` (seeded ids, OTLP http/protobuf, global provider), instrument, call the fake provider with `x-fixture-scenario`, `_otel.flush(provider)`. Imports the pinned libraries inside the function: the harness venv does not have them |

The subprocess gets `PYTHONPATH=python`, `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`,
`OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf`, `OTEL_SERVICE_NAME=spanlight-fixture`,
`OPENAI_BASE_URL` (with `/v1`), `ANTHROPIC_BASE_URL`, `OPENAI_API_KEY=sk-fixture`,
`ANTHROPIC_API_KEY=sk-fixture` and `X_FIXTURE_SCENARIO`. `record.py` sets `TMPDIR=/tmp` for itself, the venv and every
subprocess, so a stack trace in a fixture never shows a per-user temp path (macOS
`/var/folders/<hash>/T`). Exactly one OTLP export per scenario is
required: flush once, at the end. A recipe sets its own library flags inside `run()`; the
`otel-genai-python` recipe sets `OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental` and
`OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=SPAN_ONLY` so prompts and completions land on
the span (`gen_ai.input.messages` / `gen_ai.output.messages`); the legacy `true` value writes
them as log events, which the traces export never carries.

`--from-capture DIR` is for recorders that run elsewhere (the Node recipe): `DIR/<scenario>/`
holds the one export `capture.py --out` wrote for that scenario (`0001.pb` or `0001.json`), and
`DIR/recorder.json` holds `{"runtime": "node 22.12.0", "otel_sdk": "2.0.1", "loses": {...}}`
(`loses` optional). Scenarios are taken in sorted directory order.

## Provenance of `responses/`

Bodies are data. A body is either **constructed** (written by hand from the provider's API
reference; usage numbers are plausible, not observed) or **recorded** (bytes captured verbatim
from the real API by `capture_bodies.py`, which also writes a `<scenario>.provenance.txt`
sidecar with the date, model, usage and cost). A sidecar supersedes the row below.

| File | Provenance | Source |
| --- | --- | --- |
| `openai/chat.json` | constructed | copy of `backend/tests/gateway/fixtures/openai/chat.json` |
| `openai/chat_tools.json` | constructed | copy of `backend/tests/gateway/fixtures/openai/chat_tools.json` |
| `openai/chat_stream.sse` | constructed | copy of `backend/tests/gateway/fixtures/openai/chat_stream.sse` |
| `openai/responses.json` | constructed | copy of `backend/tests/gateway/fixtures/openai/responses.json` |
| `openai/responses_stream.sse` | constructed | copy of `backend/tests/gateway/fixtures/openai/responses_stream.sse` |
| `openai/error_429.json` | constructed | copy of `backend/tests/gateway/fixtures/openai/error_429.json` (a 429 cannot be captured on demand) |
| `openai/embeddings.json` | **absent, owner action** | no real body exists in the tree |
| `anthropic/messages_cache_write.json` | **absent, owner action** | no real body exists in the tree |
| `anthropic/messages_cache_read.json` | **absent, owner action** | no real body exists in the tree |
| `anthropic/messages_stream.sse` | **absent, owner action** | no real body exists in the tree |
| `anthropic/error_429.json` | **absent, owner action** | no Anthropic 429 body exists in the tree (`backend/tests/gateway/fixtures/anthropic/` has only a constructed `error_529.json`) |

## Owner action: record the missing bodies

The pilot runs kept extracted usage only, never a raw response, so the Anthropic and
embeddings bodies must be captured once. From this directory:

```bash
cd sdks/fixtures/otlp/record
uv run python capture_bodies.py --max-usd 0.05 --dry-run            # plan, no calls
set -a; source ~/.config/spanlight/record-keys.env; set +a         # ANTHROPIC_API_KEY=..., OPENAI_API_KEY=...
uv run python capture_bodies.py --max-usd 0.05
```

Keep the keys in a private file (mode 600, outside the repo) and export them from it as above
rather than typing them on the command line, where the shell history keeps them.

What it does: one `claude-haiku-4-5` call with a ~5,500-token cached system prompt (the cache
write), the same call again (the cache read), the same call streamed, and one
`text-embedding-3-small` call. Worst case as the script computes it is about $0.033; the real
spend should be close to $0.01 (cache write ≈ $0.008, two cache reads ≈ $0.001, outputs and
the embedding under $0.001). Add `--openai` (about $0.001 more) to replace the constructed
Chat Completions and Responses bodies with recordings; `--scenarios a,b` limits the run.

It refuses to run with `CI` set, stops before any call whose worst case would pass the cap,
aborts if a body contains a key-shaped string, saves `messages_cache_write` only when the usage
shows `cache_creation_input_tokens > 0` and `messages_cache_read` only with
`cache_read_input_tokens > 0` (so a re-run inside the cache's lifetime cannot mislabel a read as
a write), and writes nothing else. Keys are read from the
environment only.

Unblocked once recorded: the LiteLLM `anthropic_cache_write` and `anthropic_cache_read`
scenarios, every `openinference-anthropic` scenario, the Traceloop `embeddings` scenario, and
the Anthropic cache columns of the compatibility matrix. `anthropic/error_429.json` is not
captured by the script (a rate limit cannot be triggered on demand); construct it from the API
reference and label it constructed in the table above, or leave the scenario blocked.

## Tests and checks

```bash
cd sdks/fixtures/otlp/record
uv run pytest
uv run ruff check && uv run ruff format --check
```

`tests/test_capture_bodies.py` runs the recorder only against the fake provider and in
`--dry-run`; no test makes a provider call.
