# Ingestion spike: 429s, load, and late spans

## Purpose and when to use

Use this when ingestion traffic jumps or misbehaves:

- Senders get `429 RATE_LIMITED` from `POST /v1/traces` or `POST /v1/otlp/traces`.
- The api or database is under load after a burst of traffic.
- A sender was offline, then flushed a backlog, and charts over long windows (a week, a month) are missing spans that the trace list shows.

## Prerequisites

- Shell access and the Compose shortcut:

  ```bash
  spl() { docker compose -f deploy/compose.yaml --env-file deploy/.env "$@"; }
  ```

- For the metrics queries: a Prometheus that scrapes the api (see [slo.md](slo.md)).

## How ingestion limits work

- **Per-key rate limit.** Each API key gets 50 requests per second, with a burst of 100. The limit counts requests, not spans. It is shared by all api replicas through the `rate_limit_buckets` table in Postgres. The limits are constants in `backend/app/core/ratelimit.py`, not settings; changing them is a code change.
- **A refused request** is `429` with `Retry-After` (whole seconds) and a problem+json body whose `code` is `RATE_LIMITED` and whose `detail` is `Ingestion rate limit exceeded for this key.` The Python SDK honours `Retry-After` and retries (`max_retries`, default 5) before it drops a batch.
- **Request size.** A request may carry at most 1000 spans and 5 MiB (after decompression); beyond that it gets `413 PAYLOAD_TOO_LARGE`. So one key can in principle ingest up to 50 000 spans a second if its senders batch to the maximum.
- **Late spans.** The api accepts spans that started up to 90 days ago (and end no more than 10 minutes in the future). The rollups that long-window charts read are recomputed only for the last 48 hours.

## 1. See what is happening

Rate of `429` responses and which routes:

```promql
sum by (route) (rate(spanlight_http_requests_total{status="429"}[5m]))
sum by (scope) (rate(spanlight_rate_limit_rejections_total[5m]))
```

Spans accepted and rejected per second, by source (`native` or `otlp`):

```promql
sum by (source) (rate(spanlight_ingested_spans_total[5m]))
sum by (source) (rate(spanlight_rejected_spans_total[5m]))
```

Rejected spans are usually the sender's doing (the per-span `reason` in the response says why). One exception: a span rejected as `span: could not be processed` hit a bug while being prepared. Those are also counted, by error type, in:

```promql
sum by (error_type) (increase(spanlight_span_normalization_failures_total[1h]))
```

Anything above zero is a bug, not load. Each one logs `span_normalization_failed` with the error type, the span's index in its batch and the code location that raised (never the payload): `spl logs --since 1h api | grep span_normalization_failed`. Report it with those lines; the rest of the batch was stored.

Without Prometheus, the api logs one `http_request` line per request:

```bash
spl logs --since 10m api | grep -c '"status": 429'
spl logs --since 10m api | grep '"status": 429' | tail -3
```

Which key is being throttled? The bucket table holds the tokens left for each key. Keys near zero are the busy ones:

```bash
spl exec postgres psql -U postgres -d spanlight -c \
  "SELECT k.name, k.prefix, k.project_id, round(b.tokens::numeric, 1) AS tokens_left, b.updated_at
     FROM rate_limit_buckets b
     JOIN api_keys k ON b.key = 'ingest:key:' || k.id::text
    ORDER BY b.tokens ASC LIMIT 10"
```

A bucket refills at 50 tokens a second, so look at rows with a recent `updated_at`. The table is unlogged and pruned by the worker, so old rows disappear.

## 2. Fix 429s

429 is the system protecting the database. The remedy is on the sending side, because the limit is per key and per request:

1. **Send bigger batches, less often.** The same spans in fewer requests. Up to 1000 spans and 5 MiB per request.
2. **Give each producer its own key.** Limits are per key, so one noisy service no longer throttles another. Create one key per service or environment in the dashboard's **API keys** page (see [revoke-key.md](revoke-key.md#revoke-a-project-api-key) for where it is).
3. **Let the SDK retry.** It waits as long as `Retry-After` says, then retries, so short bursts are absorbed. Only a sustained overload drops batches.
4. **Raise the limit only with a code change.** If your real, steady traffic is above 50 requests per second per key after batching, change `INGEST_LIMITER` in `backend/app/core/ratelimit.py`, then ship it through [deploy.md](deploy.md) after checking the connection budget in [scale.md](scale.md#2-the-connection-budget).

Verify: the 429 rate returns to roughly zero, `spanlight_ingested_spans_total` keeps rising, and new spans show up in the dashboard.

## 3. If the load is on the database

A burst that stays under the limit can still be heavy: many keys, or many large batches.

1. Check the database as in [scale.md](scale.md#1-find-the-bottleneck): connections, running statements and CPU.
2. If connections are the limit, check `spanlight_rate_limit_unavailable_total` and the `rate_limit_unavailable` log lines. They mean the rate-limit pool was exhausted and the limit was skipped for those requests.
3. Add capacity: [scale.md](scale.md).
4. The worker keeps going during a spike. Charts read every hour the rollup job has not finished yet from the raw spans, so they stay current while the job catches up, as long as it is less than 24 hours behind. A span stored more than 10 minutes after it started can be missing from an hour that is already rolled up until that hour is rolled up again: within 5 minutes for the last three hours, otherwise within about an hour.

`503` responses with `Retry-After: 1` mean an api process was already serving `API_MAX_IN_FLIGHT` (default 64) `/api` and `/v1` requests: it answered at once instead of queueing, without reading the body or touching the database. Each one counts in `spanlight_admission_rejections_total`, and the process logs `admission_rejected` (with the limit and the path) at most once a minute while it lasts. A steady rate means the process is past what it can serve: add replicas as in [scale.md](scale.md) rather than raising the cap, which only brings back the queueing (slow answers, then proxy `502`s). `503` with `Retry-After: 5` is the database side instead: `db_pool_timeout` or `db_statement_timeout` in the logs (`db_connection_lost` right after a Postgres restart, once per stale pooled connection).

Replaying a batch is safe. Ingestion is idempotent: spans are upserted on their natural key and trace totals are recomputed from the stored spans, so a sender that retries after a 429 or a timeout does not double-count.

## 4. Late spans: rebuild the rollups

### When to use

Spans arrived more than 48 hours after they started. Typical causes: a sender buffered while offline and flushed days later, a collector replayed a backlog, or a project was imported. The trace explorer shows these spans (it reads raw spans), but charts that cover those hours are missing them (closed hours are read from the hourly rollups, and the response says `approximate`), because the scheduled job only recomputes the last 48 hours.

### Find the affected range

Spans ingested in the last day whose start was more than 48 hours before they arrived, per project:

```bash
spl exec postgres psql -U postgres -d spanlight -c \
  "SELECT project_id, min(started_at) AS earliest, max(started_at) AS latest, count(*) AS spans
     FROM spans
    WHERE ingested_at > now() - interval '1 day'
      AND started_at < ingested_at - interval '48 hours'
    GROUP BY project_id"
```

Run it as the database owner (as here): the app role sees only the project bound to its session, because of row-level security. Each row gives you a project id and the time range to rebuild. Widen the range by a day on each side if you are unsure. (A span that a sender re-sends has its `ingested_at` refreshed, so a replay of old data shows up here too.)

### Rebuild

```bash
spl exec worker spanlight rollups backfill \
  --project 0198f3a2-5c1e-7b3a-9d41-6f2e8a1c0b77 \
  --from 2026-09-20 --to 2026-10-02
```

Expected: `Wrote 412 rollup rows.` (your own count).

How it behaves:

- `--from` is inclusive and `--to` is exclusive. Both are ISO 8601; a value without a UTC offset is read as UTC (`2026-09-20` is midnight UTC). Both ends are widened to whole hours.
- The range may be at most 90 days. Split larger ranges into several runs.
- It works in day-sized chunks, newest first, and commits each chunk, so stopping it halfway keeps what was written. Run the same command again to finish.
- Recomputing is idempotent: running it twice gives the same rows.
- It is not bound by the worker's 50-second task limit, and each chunk is its own transaction, so it can run while the system is serving traffic.

Errors you may see:

| Message | Meaning |
|---|---|
| `no project with id <id>` | The project id does not exist. Check `SELECT id, name FROM projects`. |
| `--from must be earlier than --to` | The range is empty or reversed. |
| `the range may not be longer than 90 days` | Split the range. |
| `'<value>' is not an ISO 8601 date` | Use `2026-09-20` or `2026-09-20T13:00:00+00:00`. |

### Verify

Open the project's overview with a window that covers the affected days (for example 30 days) and check that the span count for the affected days matches the trace explorer. Or compare in SQL, as the owner:

```bash
spl exec postgres psql -U postgres -d spanlight -c \
  "SELECT (SELECT count(*) FROM spans
            WHERE project_id = '0198f3a2-5c1e-7b3a-9d41-6f2e8a1c0b77'
              AND started_at >= '2026-09-20' AND started_at < '2026-10-02') AS raw_spans,
          (SELECT coalesce(sum(span_count), 0) FROM span_rollups_hourly
            WHERE project_id = '0198f3a2-5c1e-7b3a-9d41-6f2e8a1c0b77'
              AND bucket_start >= '2026-09-20' AND bucket_start < '2026-10-02') AS rolled_up"
```

The two numbers should match.

### Escape hatch

Rollups are derived data and can be rebuilt at any time from the spans, so there is nothing to undo. If a backfill is interrupted, run it again. If spans are older than the project's retention, they have been deleted and cannot be rebuilt (a backup is the only copy: [restore.md](restore.md)).
