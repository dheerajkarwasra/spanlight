---
title: Budgets and the weekly digest
description: Set spend limits per project, gateway key, end user or model, notify or block gateway calls, and get a weekly summary by email.
sidebar:
  order: 2
---

A **budget** watches how much a scope spends in a day or a month. When the spend passes the amount, Spanlight sends a `budget.exceeded` notification to the budget's [channels](/docs/alerts/#channels), and a budget set to **block** also makes the [gateway](/docs/gateway/quickstart/) refuse further calls in that scope. Manage budgets under **Budgets** in a project.

Anyone who can see the project can read its budgets; only organization **admins and owners** can create, edit or delete them. A project can have up to 50, each with up to 10 channels, and budget names are unique within the project.

## Scope and period

| Scope | `scope_id` | Spend counted |
| --- | --- | --- |
| `project` | none | All priced spans of the project |
| `gateway_key` | The id of one of the project's gateway keys | Spans produced by calls through that key |
| `user` | An end-user id, as sent in the `x-spanlight-user` header | Spans of traces for that user |
| `model` | A model name | Spans of that model. `gpt-4o` also covers snapshot names such as `gpt-4o-2024-08-06` and `gpt-4o-latest`, but not `gpt-4o-mini` |

There are no budgets per project API key: blocking only ever happens in the gateway, and a key that sends traces with the SDK cannot be stopped.

The **period** is a UTC calendar day (`daily`, resetting at 00:00 UTC) or a UTC calendar month (`monthly`, resetting at 00:00 UTC on the 1st). The `amount` is in US dollars, above zero, with at most four decimal places.

Spend is the cost of the priced spans in the scope since the period began. A span whose model has no known price adds nothing, so spend is a lower bound when unpriced models are in use (the **Unpriced models** list shows which). The spend of a scope that has spans but no priced span at all shows as "—", never as $0.

A budget fires when spend is **strictly above** the amount. It resolves, sending `alert.resolved`, on the first evaluation that measures spend at or below the amount.

## Notify or block

| Action | When the budget is exceeded |
| --- | --- |
| `notify` | Sends the notification. Nothing is blocked. |
| `block` | Sends the notification and makes the gateway answer `402 BUDGET_EXCEEDED` to calls in the scope, with no call to the provider and no cost, until the budget is no longer exceeded. |

The notification is the same for both. It names the budget, what was spent and the amount, and for a blocking budget it says when the period resets.

### What a block does and does not do

- **Blocking lags spend by up to one evaluation.** Budgets are evaluated by the same worker pass as alert rules, once a minute, and the gateway reads that evaluated state instead of summing spend on every call. Calls made after the amount is crossed and before the next pass still go through, so a fast stream of expensive calls can overshoot. A budget is a guard rail, not a hard cap: for a limit that must never be passed, set the budget below it.
- **Ingestion is never blocked.** `POST /v1/traces`, the OTLP endpoint and the SDK keep recording whatever the budgets say. A budget limits what the gateway spends, not what the dashboard may know.
- **Only the gateway blocks.** Calls that do not go through a gateway key are not stopped.
- **Several budgets can match one call.** The call is refused if any enabled blocking budget that matches its project, gateway key, user or model is exceeded; the message names the one with the lowest amount.
- **A stopped worker means a stale guard.** If evaluation stops, a blocking budget keeps its last state: an exceeded one keeps blocking and one not yet exceeded keeps allowing.
- **Editing is immediate.** Changing a firing budget's amount, scope or period, disabling it, or deleting it resolves it at once and unblocks the scope; if it is still over the line it fires again within a minute.

### The 402 response

A blocked call gets HTTP `402` in the error shape of the surface the client used, so the official SDKs raise their usual billing exceptions and do not retry. The message names the budget and what was spent (or, when the spend is not known, the limit that was reached). See [Gateway error responses](/docs/gateway/errors/) for the common fields.

OpenAI (`/gw/v1/chat/completions`, `/gw/v1/responses`):

```json
{
  "error": {
    "message": "Blocked by a Spanlight budget: Budget \"Monthly cap\" is exhausted: $52.13 of $50.00 spent this month.",
    "type": "insufficient_quota",
    "param": null,
    "code": "insufficient_quota",
    "spanlight_code": "BUDGET_EXCEEDED"
  }
}
```

Anthropic (`/gw/v1/messages`):

```json
{
  "type": "error",
  "error": {
    "type": "billing_error",
    "message": "Blocked by a Spanlight budget: Budget \"Monthly cap\" is exhausted: $52.13 of $50.00 spent this month.",
    "spanlight_code": "BUDGET_EXCEEDED"
  },
  "request_id": "4f2c0a7e9b1d4c3f8a5e6d7c8b9a0f1e"
}
```

Both carry the header `X-Spanlight-Code: BUDGET_EXCEEDED`. Match on `spanlight_code`, not on the message.

### Rollover

At 00:00 UTC on the first day of a new period, a blocking budget stops blocking, even before the next evaluation has resolved it. The first evaluation of the new period resolves the budget whatever the new spend is (spend of blocked or unpriced calls is not known), and it fires again only if the new period's spend is already above the amount. The `state` of a budget in the first minute of a period shows "—" until it is evaluated.

## Try it

A budget of `$0.01` is the quickest way to see a block. Create a blocking budget on the project, send a few calls through a gateway key, wait for the next evaluation (up to a minute), and the next call is refused with `402`.

## The weekly digest

Every **Monday at 08:00 UTC**, each organization member with a verified email address gets one message per project with a summary of the week that ended at 00:00 UTC that day (seven whole UTC days, compared with the seven before): spend, LLM calls, error rate and p95 latency with their changes, the top five models by cost, and how many alerts fired.

- **Turn it off per project** with **Weekly digest email** in project settings (admins and owners). It is on by default.
- **Quiet projects are skipped.** A project is included only when it received at least one span during the week.
- **It needs an email provider.** Without one, the digest job ends as skipped and sends nothing. Operators can switch the digest off for the whole deployment with `WEEKLY_DIGEST_ENABLED`.
- **Unknown figures show "—"** with no change, and a change against an unknown or zero earlier value reads "new". The shared demo account and the demo organization are never mailed.

If a run is interrupted, the retry sends only to projects that were not yet queued, so nobody gets a project twice.

## Settings

Budgets are evaluated by the alert evaluation job, so they follow `ALERTS_EVALUATION_ENABLED`; the digest follows `WEEKLY_DIGEST_ENABLED`. See [Alerts](/docs/alerts/#settings) and [Configuration](/docs/configuration/#alerts).

## Troubleshooting

| You see | Check |
| --- | --- |
| A blocking budget did not block | Evaluation lags up to a minute (see above). Check that the budget is enabled, that its scope matches the call (the gateway key id, the exact `x-spanlight-user` value, the model the client asked for or the route's resolved model), and that spend is above the amount: a model without a known price adds nothing. |
| Spend shows "—" | The budget has not been evaluated yet (it shows within a minute), or the scope has spans and none is priced. |
| Calls are blocked and you want them back now | Raise the amount, disable the budget or delete it. The scope is unblocked straight away. |
| No digest arrived | An email provider must be configured, the project's toggle must be on, the project must have had traffic that week, and your email must be verified. |
| Evaluation is late or stopped | See the [alerts runbook](/docs/runbooks/alerts/), in particular [evaluation lag](/docs/runbooks/alerts/#evaluation-lag). |
