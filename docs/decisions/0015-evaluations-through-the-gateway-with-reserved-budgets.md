# 15. Evaluations run through the gateway under reserved budgets

Date: 2026-10-10 · Status: accepted

## Context

An LLM-judge evaluator scores traces by calling a model: it samples a share of new traces every minute, unattended, with the organization's provider credential. A misconfigured sampling rate, a traffic spike or a retry storm would turn into a provider bill nobody approved. Playground runs and experiments make the same kind of paid call from inside Spanlight.

Three questions had to be answered. **Which path does the call take?** A direct provider SDK call would be invisible to Spanlight's own observability, skip the project's spend budgets and need its own credential handling. **What stops overspend?** A check of "spent so far" before each call is not enough: many runs start in the same minute, each sees the same sum, and all pass the edge together. **What counts as spent when a call fails?** A timeout or a dropped connection may still have been billed by the provider.

## Decision

Every evaluation call goes through the gateway, under a per-evaluator monthly budget enforced by a reservation taken before the call.

- **Through the gateway, on a route built in memory.** A judge names a credential and a model, not a stored route. The call runs `execute` in process with no gateway key (no cache, faults or key limits) through a single-target route with one attempt and no fallbacks: the reservation covers exactly one call, and a retry after a dropped connection could be billed twice. The trace lands in the project like any other call (environment `evals`, tag `spanlight:judge`), and the project's `block` budgets still apply because it is real spend there. The sampler never samples these traces, so a judge cannot evaluate itself.
- **A budget is mandatory.** A judge cannot be created without a positive `monthly_budget_usd`, nor with a model the price book cannot price, because an unpriced call could not be counted.
- **Estimate as a ceiling.** Before each call, the cost is estimated from the price book: one input token per three characters of the compiled prompt (inputs and outputs are truncated before compiling, so the estimate is tight) plus the whole `max_tokens` allowance as output.
- **Reserve under a lock.** In one short transaction under an advisory lock per evaluator, the month's committed spend is the settled cost of finished runs plus the reservations of running ones. If that plus the estimate exceeds the budget, the run fails with `budget_exceeded` and no provider request is made. Otherwise the run holds the estimate as `reserved_usd` and the transaction commits before the call, so no connection is held while the provider answers and the next run sees the reservation.
- **Settle what was really spent.** After the call, in a session of its own and shielded from cancellation, the run is charged the price-book cost of its reported usage and its reservation is released. A failure that may have been billed (a timeout, a connection error, a cancellation, an unparseable verdict) is charged its usage or, without usage, the full estimate. Only a call that was never sent, or that the provider refused with an HTTP error status, is charged nothing.
- **Reap the dead.** A run whose worker crashed keeps its reservation until a reaper, at the start of every sampling cycle, fails runs with no heartbeat for ten minutes and charges them the reservation.

## Consequences

- **Spend never exceeds the budget by more than one estimate.** Concurrent runs serialize on the lock and see each other's reservations, so two cannot both pass the edge. The only slack is a call that settles below its estimate, and it is returned to the budget.
- **The budget errs high.** Ambiguous failures and abandoned runs are charged their worst case, so the reported spend can exceed what the provider billed, never the other way round.
- **Judging is observable.** Judge calls are ordinary gateway spans: their latency, tokens, cost and errors show up in the project's traces and metrics, and the Doctor's detectors see them.
- **A budget per evaluator, not per organization.** Two judges have two budgets. An organization-wide ceiling still comes from the project's gateway `block` budgets, which stop the call (`gateway_budget_blocked`) before it is sent.
- **One lock per evaluator.** The reservation transaction is short, but a judge sampling hundreds of traces a minute serializes them at that point. This is far below the provider's own rate limits and is accepted.
