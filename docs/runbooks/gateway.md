# Run the LLM gateway

## Purpose and when to use

The gateway serves `/gw/v1/*`: applications point an OpenAI or Anthropic client at it with a gateway key (`spl_gw_...`), and every call is traced, priced, rate limited and, if you configure it, routed, retried, cached and fault-injected. Use this page to:

- decide whether the gateway runs inside the api or as its own process, and switch between the two;
- rotate `CREDENTIALS_KEYS`, the master keys that seal provider credentials;
- purge a project's response cache;
- revoke a gateway key;
- read the overhead panel and the other gateway panels on the Grafana dashboard.

## Prerequisites

- `CREDENTIALS_KEYS` set on the api and the worker. Without it, saving a provider credential answers `409 NOT_CONFIGURED`. See [rotate-secrets.md](rotate-secrets.md#credentials_keys).
- Write access to `deploy/.env` (Compose) or the Railway project.
- Docker Compose shortcut:

  ```bash
  spl() { docker compose -f deploy/compose.yaml --env-file deploy/.env "$@"; }
  ```

- `$BASE` is the public address of the dashboard, as in the [overview](README.md#conventions-used-in-these-pages).

## Embedded or standalone

| Mode | `GATEWAY_MODE` | What serves `/gw/*` | Choose it when |
|---|---|---|---|
| Embedded (default) | `embedded` or unset | The `api` service | Most deployments. One process fewer to run, and Railway runs it this way. |
| Standalone | `standalone` | A separate `gateway` service (`python -m app.gateway`, same image) | LLM traffic should scale or fail apart from the dashboard API, for example long streamed answers holding many connections. |
| Off | `disabled` | Nothing | You do not want the gateway at all. |

In `standalone` and `disabled` mode the api answers `/gw/*` with `404`. Caddy in the `web` service sends `/gw/*` to `GATEWAY_UPSTREAM`, which defaults to `api:8000`, with response streaming switched on (`flush_interval -1`) and an 11 MiB limit on request bodies, a little above the gateway's own 10 MB, so an oversized body gets the gateway's provider-shaped `413`. Every other path goes where it always did.

`/health/ready` reports the mode as `gateway_mode`.

### Switch to standalone (Compose)

1. In `deploy/.env` set:

   ```
   GATEWAY_MODE=standalone
   GATEWAY_UPSTREAM=gateway:8000
   ```

   Both settings must change together. `GATEWAY_MODE` is read by the api and the gateway; `GATEWAY_UPSTREAM` is read by `web`.

2. Start the stack with the `standalone-gateway` profile:

   ```bash
   spl --profile standalone-gateway up -d --build
   ```

   To keep the profile on for every later command, set `COMPOSE_PROFILES=standalone-gateway` in `deploy/.env` instead.

3. Verify. A call without a key must be refused by the gateway, in the shape of the SDK it speaks to:

   ```bash
   curl -sS -X POST "$BASE/gw/v1/chat/completions" -H 'Content-Type: application/json' -d '{}'
   curl -sS -X POST "$BASE/gw/v1/messages" -H 'Content-Type: application/json' -d '{}'
   ```

   Expected: HTTP `401` for both. The first body has `"error":{"type":"invalid_request_error",...}`; the second has `"type":"error"`. Then check that the gateway, and not the api, took the request:

   ```bash
   spl logs --since 2m gateway | grep '/gw/v1/'
   spl logs --since 2m api | grep -c '/gw/v1/'
   ```

   Expected: lines from `gateway`, `0` from `api`. `spl ps` shows `gateway` as `healthy`, and `curl -sS "$BASE/health/ready"` reports `"gateway_mode":"standalone"`.

### Switch back to embedded

Set `GATEWAY_MODE=embedded` (or remove the line) and `GATEWAY_UPSTREAM=api:8000` (or remove the line), then stop the gateway container and recreate the rest. `--remove-orphans` does not touch a service of a profile that is switched off, so remove it by name:

```bash
spl --profile standalone-gateway rm -sf gateway
spl up -d
```

Drop the profile from your later commands (and `COMPOSE_PROFILES`, if you set it). Verify with the two `curl` commands above and with `spl logs --since 2m api`, which now shows the `/gw/v1/` lines.

### Railway

The project runs the gateway embedded in `api`, with `GATEWAY_ORG_RPM_CEILING=60` declared in `deploy/railway/railway.ts` so that no organization can send more than 60 gateway requests a minute and crowd out the others. Change that number there and apply the file; a value set only with `railway variable set` is deleted by the next apply.

## Rotate CREDENTIALS_KEYS

Provider credentials are sealed under the first key of `CREDENTIALS_KEYS` and opened under whichever key sealed them. To retire a key you add the new one, make it first, re-seal what the old one sealed, and only then drop the old one.

**Back up the whole value first, away from the database backups.** A key that is lost cannot be recovered, and every credential sealed under it must be entered again.

1. Generate a key with a new id and save it in your password manager:

   ```bash
   echo "v2:$(openssl rand -base64 32)"
   ```

2. Append it after the old one on the api, the worker and the gateway (if you run it standalone), then restart them: `CREDENTIALS_KEYS=v1:<old>,v2:<new>`.
3. Put it first on all of them and restart again: `CREDENTIALS_KEYS=v2:<new>,v1:<old>`. New credentials are now sealed under `v2`.
4. Re-seal the existing credentials:

   ```bash
   spl exec worker spanlight reseal-credentials
   ```

   Expected: `Re-sealed <n> provider credentials and <m> billing source keys.` and exit status 0. The command recounts afterwards and exits non-zero while any credential or billing source is still sealed under an older key. A credential or billing source that no key in the list can open is left as it is and reported on standard error with a count; its id is in the log. That credential has to be replaced in the dashboard, and that billing source's admin key entered again.
5. Only after step 4 succeeds, remove `v1` from the value on every service and restart them.

Verify: send a call through a route that uses a credential. It must reach the provider and be answered; a credential that cannot be opened fails the call and is logged by the api or gateway. Roll back before step 5 by putting `v1` first again; nothing is lost while both keys are in the list.

## Purge a project's cache

The cache is an exact-match store of non-streaming `200` answers, switched on per gateway key. Purge it after you change a prompt template that clients rely on, or when a cached answer must not be served again.

```bash
read -rs TOKEN; echo
curl -sS -o /dev/null -w '%{http_code}\n' -X POST \
  -H "Authorization: Bearer $TOKEN" "$BASE/api/v1/projects/<project-id>/gateway/cache/purge"
unset TOKEN
```

`TOKEN` is a personal access token with the `write` scope, owned by an admin or owner of the organization. Expected: `204`. Purging an empty cache also gives `204`. The project's audit log gets a `gateway_cache.purge` event with the number of entries removed. The next identical request is a miss: its response carries `X-Spanlight-Cache: miss`.

## Revoke a gateway key

A gateway key (`spl_gw_...`) is separate from the project's ingest keys; revoking one does not affect the others. Revocation is immediate, because every call looks the key up in the database.

1. Sign in, open the project, go to **Gateway**, find the key by name or prefix and revoke it. Or with the API, with the same kind of token as above:

   ```bash
   curl -sS -o /dev/null -w '%{http_code}\n' -X DELETE \
     -H "Authorization: Bearer $TOKEN" "$BASE/api/v1/projects/<project-id>/gateway/keys/<key-id>"
   ```

   Expected: `204`. Repeating it is harmless.
2. Create a replacement and give it to the application. The secret is shown once.

Verify: a call with the old key answers `401`. If the key leaked, the provider credentials behind it did not: they never leave the server. Still, read the key's usage in the dashboard to see what was sent, and consider rotating the provider credential at the provider if the key could reach a costly model.

## Read the gateway panels

The Grafana dashboard (`deploy/grafana/spanlight-dashboard.json`) has an **LLM gateway** row. Scrape every process that serves the gateway: the api, or the `gateway` service in standalone mode (the [overview](README.md#services-at-a-glance) explains how `/metrics` is scraped).

| Panel | Metric | What to look for |
|---|---|---|
| Gateway requests by outcome | `spanlight_gateway_requests_total` | `ok` should dominate. `upstream_error` rising means a provider is failing; `gateway_error` means calls the gateway refused or failed itself; `fault` is traffic the Lab injected on purpose; `budget_blocked` is calls stopped by a spending limit. |
| Gateway overhead (p95) | `spanlight_gateway_overhead_seconds` | Time a call spent in the gateway itself, from the key check to the answer, without the provider's time and without retry waits, by surface. The target is under 20 ms; the line on the panel marks it. A rise with a flat request rate points at database latency (the key lookup, rate-limit and budget checks use it) or at a starved api; see [scale.md](scale.md). |
| Upstream attempts by status | `spanlight_gateway_attempts_total` | One series per provider and status. `429` and `5xx` series show which provider is limiting or failing; `timeout` and `connection_error` mean the network or the provider's edge; `blocked` means the egress check refused a base URL (a private address while `GATEWAY_ALLOW_INSECURE_BASE_URLS` is off); `cancelled` means the caller went away while the provider was still answering; it is not a provider failure, so leave it out of any error ratio you compute from this series (`status!~"2..|cancelled"`). |
| Gateway cache hits | `spanlight_gateway_requests_total{outcome="cache_hit"}` | Hits per second and their share of all calls. A share near zero on a key that has a cache TTL means clients vary the request (a changing timestamp in the prompt, for example). |
| Gateway spans not recorded | `spanlight_gateway_record_failures_total` | Calls that were answered but whose trace was not written. `overflow` means the write backlog was full: raise `GATEWAY_RECORD_CONCURRENCY` (each write holds a database connection, so check the pool in [scale.md](scale.md) first) or `GATEWAY_RECORD_BACKLOG`. `error` means the write failed for good; look at the api log, where each `gateway.record_failed` line names the error type, the SQLSTATE of a database error and the number of tries, never the message. A transient failure (a lost connection, a timeout, a deadlock) is retried up to four tries over about 2.6 seconds, each retry logging `gateway.record_retry`. A failure a retry would meet again is not retried: a value or constraint the database refuses (`DataError`, `IntegrityError`, `ProgrammingError`; a bug to report), a deleted project, a span whose input or output could not be prepared for the queue, or a span that could not even be built (logged as `gateway.span_failed` with `stage=build` and the error type; a bug to report). `rejected` means ingestion refused the span (see the api log for the reason). Anything above zero for long is a gap in the traces. Without Prometheus, `gateway_spans_lost` on `/health/ready` is the same count for that process since it started. |

## Budget reservations

A gateway call that matches a `block` budget reserves its estimated cost before it is forwarded and settles the real cost when its span is written ([ADR 0017](../decisions/0017-gateway-budgets-reserve-before-forwarding.md)). Watch these metrics:

| Metric | What to look for |
|---|---|
| `spanlight_budget_blocks_total{reason}` | `reserved` is a call refused because its estimate did not fit; `firing` is a budget the evaluator measured as exhausted (SDK or OTLP spend counts there). |
| `spanlight_budget_reserve_seconds` | Time of the reserve step. Calls on one budget wait for each other's row lock, so a burst on one budget shows a long tail. A p95 over 5 ms at normal load is the signal to act. |
| `spanlight_budget_reservations_held` | Reservations in flight. It should follow the number of calls running; a steady climb means spans are not being written (see **Gateway spans not recorded** above). |
| `spanlight_budget_reservations_reaped_total` | Reservations the worker counted at their estimate after 12 minutes without a settle: a crash, or a span dropped because the write backlog was full (a span whose write failed for good is settled at once). Spend then reads high until a late settle corrects it. |

The reaper (`reap_budget_reservations`) runs every minute on the worker; with the worker stopped, expired reservations keep holding budget until it runs again.

## Roll back

Standalone to embedded: see above. A key revocation cannot be undone from the dashboard; create a new key. A purged cache refills by itself as requests arrive. A `CREDENTIALS_KEYS` rotation is undone by putting the old key first again, as long as the old key is still in the list.
