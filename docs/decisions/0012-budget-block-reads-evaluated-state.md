# 12. Budget blocking reads the evaluated state

Date: 2026-10-10 · Status: accepted

## Context

A budget caps the spend of a project, a gateway key, an end user or a model over a UTC day or month. One that is set to `block` has to stop gateway calls once the cap is reached, with no upstream call and no cost.

Two designs were considered. The guard could **sum the spend on every call**, which is exact but adds an aggregate over spans to the hot path of every request, and the answer it gives can disagree with what the notification said a minute earlier. Or it could **read the state the alert evaluator already computed**: each budget owns a hidden alert rule that measures its spend on every evaluation pass, and the rule's stored state says whether the budget is exhausted.

## Decision

Block by reading the evaluated state.

- **One indexed query per call.** The gateway's budget guard joins the project's enabled `block` budgets whose scope matches the call (`project`; `gateway_key` equal to the key; `model` equal to the model the client asked for or to the model any of the route's targets would send upstream after its alias; `user` equal to the end-user id) with their rule's stored state, and refuses the call when one is `firing`. The lowest amount is reported when several match. The query runs in the short transaction the gateway already opens for the check, under the project binding, so row-level security applies.
- **No per-call sums.** The guard never touches spans. Its cost per call is a lookup of a few rows by `(project_id, scope, scope_id)` and a primary-key join.
- **The same stored state as the notification.** A budget blocks exactly while its rule's stored state is `firing`, so the budgets page, the alert and the gateway read the same state rather than three separate calculations. They can differ only by the time it takes the next evaluation to write it.
- **Model names match like prices do.** A `model` budget on `gpt-4o` also covers `gpt-4o-2024-08-06` and `gpt-4o-latest` (a date or `-latest` suffix), but not `gpt-4o-mini`. The guard, the budget's spend measurement over spans and over rollups, and the price table share this one rule, so a budget measures the snapshot names providers report in their responses.
- **Only the current period blocks.** The query also requires that the state became `firing` within the budget's current UTC day or month. A state left over from a period that has ended stops blocking at the rollover, before the next evaluation resolves it.
- **Provider-shaped refusal.** A blocked call gets HTTP 402 with the code `BUDGET_EXCEEDED` in the error envelope of the surface the client used, and no attempt is made upstream. The message names the budget and what was spent. In-process callers (no gateway key) are checked against the `project`, `model` and `user` scopes only.
- **Ingestion is never blocked.** Traces sent to `/v1/traces` or the OTLP endpoint are recorded whatever the budgets say; a budget limits what the gateway spends, not what the dashboard may know.
- **Observability.** `spanlight_budget_blocks_total` counts refused calls.

## Consequences

- **Blocking lags the spend by up to one evaluation interval.** The alert worker evaluates every 60 seconds, so calls made after the cap is crossed and before the next pass still go through. A small overrun is possible, larger the faster the traffic.
- **A firing budget keeps blocking until it is measured below the amount.** The rule changes state only on a measured value; while a value cannot be measured, the last state stands. Editing or disabling the budget resolves the state at once.
- **A stopped worker means a stale guard.** If evaluation stops, every budget keeps its last state: an exceeded one keeps blocking and one not yet exceeded keeps allowing, however the spend moves. The alert evaluation metrics show it.
- **A hard cap is not promised.** For a strict limit, set the budget below the amount that must never be exceeded. A budget of $0.01 is a convenient way to see the block while trying the gateway: send a few calls, wait for the next evaluation, and the next call is refused.
- **Boring by default.** No cache, counter or extra table for spend; the guard reads rows the evaluator already writes.

## Update 2026-10-12

The gateway writes each call's span after the answer, and the budgets are evaluated on those spans, so a span that is lost makes recorded spend too low. The recorder now retries a failed write after 0.1, 0.5 and 2 seconds (four tries in all, a span for a deleted project is not retried) before it counts the span as lost, and each retry first checks that an earlier try was not stored after all, so a call is never counted twice. A span still lost, refused by ingestion or dropped because the write backlog was full is counted in `spanlight_gateway_record_failures_total` and in `gateway_spans_lost` on `/health/ready`, which never makes the probe fail. The backlog default grew from 1000 to 5000 spans.

The demo traffic job used a guard that allowed everything. It now goes through the same budget guard as any gateway call, so a firing `block` budget on the demo project stops the demo's paid call before it is sent, and the job ends as skipped rather than failed.

Consequence worth stating: spend counts priced spans only, so a budget on a model with no price never fires. Its spend is unknown, not zero, and the guard has nothing to read.
