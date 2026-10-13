# 4. Ingestion writes synchronously and idempotently

Date: 2026-10-07 · Status: accepted

## Context

SDKs retry failed exports, OTLP collectors resend batches, and networks drop responses after the server has committed. Duplicated spans would inflate costs and token counts.

## Decision

Each batch is validated, normalized, redacted and priced, then written in one transaction:

- Spans are upserted on their natural key `(project_id, trace_id, span_id)`.
- Trace totals (span count, errors, tokens, cost) are **recomputed from the stored spans**, never incremented.

Replaying any batch therefore yields identical totals. The endpoint returns per-span results, so one invalid span does not reject its whole batch.

We write synchronously instead of putting a queue in front. At the expected volume a 100-span batch commits well within the 200 ms p95 target, and a synchronous write means a `200` response is a durable acknowledgment.

## Consequences

Ingestion throughput is bounded by Postgres write capacity. If the latency target is missed, the next step is a durable staging table drained by the worker, which keeps the same idempotent upsert.

## Update 2026-10-12

The per-span results now cover more than invalid input. A value that is valid JSON but that the database cannot store rejects its span and never the batch: a timestamp whose offset moves it outside years 1 to 9999, a `time_to_first_token_ms` larger than a double, and a cost of $1,000,000 or more. Any other unexpected failure while normalizing one span rejects that span alone as `span: could not be processed`; it is logged by error type, never by message or payload, and counted in `spanlight_span_normalization_failures_total`. Only the database write stays all-or-nothing: a failed statement rolls the whole batch back, answers a non-2xx and is safe to replay.

Two limits follow from recomputing and replaying. A trace whose spans sum to $1,000,000 or more stores `NULL` as its cost, since the column cannot hold it. A replay leaves every column as it was except `ingested_at`, and prices the spans again with the prices in force at that moment. Both are listed in `docs/api-deviations.md`.
