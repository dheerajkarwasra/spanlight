import type { Budget, BudgetAction, BudgetPeriod, BudgetScope } from "@/lib/api";
import { parseMoney } from "@/lib/format";

/*
 * Pure display logic for the budget list: the money, the progress bar and the time to the reset.
 * Amounts stay decimal strings everywhere else; they are read as numbers here only to draw.
 */

export const PERIOD_LABELS: Record<BudgetPeriod, string> = { daily: "Daily", monthly: "Monthly" };

export const SCOPE_LABELS: Record<BudgetScope, string> = {
  project: "Project",
  gateway_key: "Gateway key",
  user: "End user",
  model: "Model",
  metadata: "Metadata",
  trace_name: "Trace name",
};

export const ACTION_LABELS: Record<BudgetAction, string> = {
  notify: "Notifies only",
  block: "Blocks gateway calls",
};

/** Spec §11.1: why the spend is "—" before the first evaluation. */
export const NOT_EVALUATED_REASON = "Not evaluated yet. Spend shows within a minute.";

const usdFormat = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  // Amounts keep up to 4 decimals (numeric(14,4)): $0.0125 must not read "$0.01".
  maximumFractionDigits: 4,
});

/** "$52.31", "$0.0125". Amounts under a hundredth of a cent read "< $0.0001". Null if unknown. */
export function formatUsd(value: string | null): string | null {
  const amount = parseMoney(value);
  if (amount === null) {
    return null;
  }
  if (amount > 0 && amount < 0.0001) {
    return "< $0.0001";
  }
  return usdFormat.format(amount);
}

/** Spend as a share of the amount (1.05 = 105 %). Null when either is unknown or the amount is 0. */
export function spendRatio(spent: string | null, amount: string): number | null {
  const spentNumber = parseMoney(spent);
  const amountNumber = parseMoney(amount);
  if (spentNumber === null || amountNumber === null || amountNumber <= 0) {
    return null;
  }
  return spentNumber / amountNumber;
}

/**
 * "105 %": whole percent, not capped, rounded down so 99.6 % never reads "100 %". The tiny
 * epsilon absorbs float error, so $29 of $100 (0.29 * 100 = 28.999…) still reads "29 %".
 */
export function percentLabel(ratio: number): string {
  return `${Math.floor(ratio * 100 + 1e-9)} %`;
}

/** The bar's fill: the ratio capped at 1, never below 0. */
export function barFill(ratio: number): number {
  return Math.min(Math.max(ratio, 0), 1);
}

/** Strictly over the amount, as the server fires (`spend > amount`): the danger colour. */
export function isExceeded(ratio: number | null): boolean {
  return ratio !== null && ratio > 1;
}

/** A firing `block` budget is stopping gateway calls right now. */
export function isBlocking(budget: Pick<Budget, "action" | "state">): boolean {
  return budget.action === "block" && budget.state?.state === "firing";
}

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

/**
 * "Resets in 3 d 4 h" a day or more ahead, "Resets in 15 h 20 min" under a day, "Resets in
 * 20 min" under an hour, "Resets in under a minute" at the very end. A reset time already past
 * (the next evaluation has not run yet) reads "Resets now". Null for an unreadable time.
 */
export function resetsInLabel(resetsAt: string, now: Date = new Date()): string | null {
  const at = new Date(resetsAt).getTime();
  if (Number.isNaN(at)) {
    return null;
  }
  const left = at - now.getTime();
  if (left <= 0) {
    return "Resets now";
  }
  if (left >= DAY) {
    return `Resets in ${Math.floor(left / DAY)} d ${Math.floor((left % DAY) / HOUR)} h`;
  }
  if (left >= HOUR) {
    return `Resets in ${Math.floor(left / HOUR)} h ${Math.floor((left % HOUR) / MINUTE)} min`;
  }
  if (left >= MINUTE) {
    return `Resets in ${Math.floor(left / MINUTE)} min`;
  }
  return "Resets in under a minute";
}
