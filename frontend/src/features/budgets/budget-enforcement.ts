import type { BudgetScope } from "@/lib/api";

/*
 * How a `block` budget behaves (spec 2026-10-25 "Hard admission for gateway budgets", ADR 0017):
 * the gateway reserves each call's estimate before forwarding it and refuses a call that would
 * take spend, plus the calls in flight, past the amount. SDK and OTLP traffic is never blocked;
 * it is measured by the budget's evaluation. Every surface that describes blocking reads these
 * sentences.
 */

/** How often budgets are evaluated (the backend's `guard.EVALUATION_INTERVAL_S`). */
const EVALUATION_INTERVAL_S = 60;

/** What a refused gateway call gets; the dialog sets it in mono. */
export const BLOCKED_RESPONSE = "402 BUDGET_EXCEEDED";

/*
 * Exactly what `budgets/guard.py` does (G-007 is about honest wording): a firing budget refuses
 * every gateway call, unpriced ones included; otherwise the reserve step admits a call it cannot
 * price without counting it (ADR 0012), and the estimate it reserves counts text only.
 */
const REFUSAL = `A gateway call that would take spend, plus calls in flight, past the amount gets ${BLOCKED_RESPONSE} before it reaches the provider. A call to a model without a price is not counted and is admitted unless the budget is already firing. The estimate counts text only, so images, files and audio are not held until the call settles.`;

/** `budgets/service.py` refuses an enabled monthly block budget under 31 days of retention. */
const RETENTION = "A monthly block budget needs at least 31 days of project retention.";

/** Only gateway calls spend in a gateway-key scope, so its cap is hard (spec D2). */
function isGatewayOnly(scope: BudgetScope): boolean {
  return scope === "gateway_key";
}

/** The dialog hint for action = Block. */
export function blockHint(scope: BudgetScope): string {
  return isGatewayOnly(scope)
    ? `Hard limit for priced gateway calls. ${REFUSAL} ${RETENTION}`
    : `${REFUSAL} SDK calls in this scope are measured every ${EVALUATION_INTERVAL_S} seconds, not stopped; to cap them, set a spend limit with your provider. ${RETENTION}`;
}

/** The badge of a firing `block` budget and its tooltip. */
export function blockingBadge(scope: BudgetScope): { label: string; tooltip: string } {
  return isGatewayOnly(scope)
    ? {
        label: "Blocking",
        tooltip: `Gateway calls in this scope get ${BLOCKED_RESPONSE} until the period resets or the amount is raised.`,
      }
    : {
        label: "Blocking (soft)",
        tooltip: `Soft limit. Gateway calls in this scope get ${BLOCKED_RESPONSE}. SDK calls are measured every ${EVALUATION_INTERVAL_S} s, not stopped.`,
      };
}

/** The budgets page note under the list. */
export function enforcementNote(): string {
  return "Block budgets refuse a gateway call that would take spend, plus calls in flight, past the amount. SDK calls are measured, not stopped; to cap them, set a spend limit with your provider.";
}
