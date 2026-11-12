# Integration Lab example clients

Two small OpenAI-SDK clients that show what the Spanlight Doctor looks for.

- `naive_client.py` is careless on purpose. It retries immediately on almost any error and
  ignores `Retry-After`, drops a rejected request parameter and resends, treats the end of a
  stream as the end of the answer, and gives up on a call after 2 seconds only to ask again.
- `corrected_client.py` is the same client with those habits fixed. It waits out `Retry-After`
  on a 429 plus 0.1 s (three attempts; a longer wait than 30 s is an error, not a wait), backs off 1 s then 2 s on a 5xx (each with up to 25% jitter), never retries a 400,
  401 or 403, raises out of `run` on a 400, 401, 403 or a stream with no terminal `finish_reason`
  (`IncompleteStreamError`), and allows a call 60 s without retrying a timeout.

Both talk to the Spanlight LLM gateway, so every call is traced. The gateway key must belong to a
non-production environment and have a fault profile (Gateway, Integration Lab) that injects the
failure you want to try. The clients do not create failures themselves; the scenario only shapes
the request (`truncated_stream` streams, `unsupported_parameter` sends `top_k`).

## Run

```bash
pip install openai httpx
export SPANLIGHT_GATEWAY_KEY=...        # a gateway key with a fault profile

python examples/naive_client.py --scenario rate_limited --rounds 5
python examples/corrected_client.py --scenario rate_limited --rounds 5
```

Options: `--scenario` (default `healthy`), `--rounds` (default 5), `--base-url` (default
`http://localhost:8000/gw/v1`), `--model` (default `gpt-4o-mini`) and `--api-key` (default
`$SPANLIGHT_GATEWAY_KEY`).

Each run uses one session id (header `x-spanlight-session`), each round one trace id
(`x-spanlight-trace-id`), and every call is tagged `lab:naive` or `lab:corrected`. The command
prints one line per round and a summary with the number of HTTP attempts made.

## What the Doctor should say about the naive client

| Scenario (fault profile) | Finding |
| --- | --- |
| `auth_expired`, `scope_denied`, `malformed_json`, `provider_5xx` | `retry_storm` |
| `rate_limited` (`retry_after_s: 2`) | `retry_after_ignored`, `retry_storm` |
| `truncated_stream` | `truncated_stream_accepted` |
| `unsupported_parameter` (`param: "top_k"`) | `unsupported_parameter_retried` |
| `slow_response` (`delay_ms: 5000`), `timeout` (`hold_ms: 30000`) | `client_timeout_misconfigured`, `retry_storm` |

The corrected client should open no finding in any of these scenarios (keep `hold_ms` under
60000, or its own 60 s timeout becomes the clustered abort). Findings appear after the
Doctor's next run (every 15 minutes) and some need a minimum number of samples, so use a few
rounds (10 or more for the timeout scenarios).

## Use in your own tests

`run(scenario, *, base_url, api_key, rounds, model="gpt-4o-mini", http_client=None)` in either
module returns a `RunReport(session_id, attempts, errors, outcomes)`. Pass an `httpx.Client` as
`http_client` to route the calls through your own transport.
