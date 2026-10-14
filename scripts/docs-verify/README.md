# Documentation sample verification

Helpers for running documentation samples end to end against a local Spanlight stack, with a
mock LLM provider instead of real provider keys.

| Script | What it does |
| --- | --- |
| `mock_provider.py` | A stand-in for OpenAI (`/v1/chat/completions`) and Anthropic (`/v1/messages`), streaming or not. The answer is always `mock answer to: <last user message>`; the response echoes the requested model and reports usage as a word count (never zero). Calls must carry the key `docs-verify-key`. |
| `bootstrap.py` | Creates, through the product's own API, a user, organization, project, ingest API key, personal access token, gateway credential pointing at the mock, route and gateway key. Writes `.local/docs-verify.env` (git-ignored) and prints only the variable names. Each value is saved as soon as it exists, so a run that fails halfway is resumed by running it again. |
| `check_trace.py` | Fetches the newest trace of that project (or `--trace-id`) and prints one JSON object with the fields the documentation promises: trace name, environment, release, user, session, cost, and per span kind, provider, model, tokens, cost, status, finish reason and attribute names. Counts only traces from the last two minutes (or since `--since`), and prints once the span count has stopped growing. Exits 1 if no trace arrives within `--wait` seconds, 2 if the API refuses or fails (for example 401). |

## Run it

1. Start the API with the two settings the mock needs, and a worker if the sample uses one:
   * `GATEWAY_ALLOW_INSECURE_BASE_URLS=true`, so a credential may point at `http://127.0.0.1:9900`.
   * `CREDENTIALS_KEYS` set to a key such as `v1:$(openssl rand -base64 32)`, so the credential's
     key can be encrypted at rest.
2. Start the mock: `python scripts/docs-verify/mock_provider.py --port 9900`.
3. Create the account and keys: `uv run --script scripts/docs-verify/bootstrap.py`
   (it uses the API at `http://127.0.0.1:8000`, the development default; pass `--host <address>` or set the
   `SPANLIGHT_HOST` environment variable for another address).
4. Load the values into your shell, run the sample, then look at what arrived:

   ```bash
   set -a; . .local/docs-verify.env; set +a
   uv run --script scripts/docs-verify/check_trace.py --since "$(date -u +%Y-%m-%dT%H:%M:%S+00:00)"
   ```

   Take the timestamp before you run the sample. Without `--since`, only traces that started in the
   two minutes before `check_trace.py` began count, so an old trace is never mistaken for the new
   one; `--since` makes that exact. The script waits until the span count of the trace stays the
   same for two polls, because the spans of one trace can arrive in separate batches. Use
   `--trace-id` to inspect one particular trace.

The gateway credential is of the `openai_compatible` kind, because that is the only kind that
accepts a base URL. Gateway spans therefore report the provider `openai_compatible`, and their
cost is `null` (no price is known for it).

## Tests

The mock provider is tested against the real `openai` and `anthropic` clients:

```bash
uv run --with pytest --with httpx --with openai==3.22.0 --with anthropic==1.9.0 \
  pytest scripts/docs-verify -q
```
