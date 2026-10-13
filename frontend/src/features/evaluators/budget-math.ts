import type { EvaluatorBudget, EvaluatorBudgetSummary } from "@/lib/api";
import { parseMoney } from "@/lib/format";

/** Spec §11: the meter turns to the warning tone at 80 % of the month's budget. */
export const WARNING_RATIO = 0.8;

export const WARNING_COPY = "80% of this month's budget is used.";

export type BudgetTone = "normal" | "warning" | "spent";

export interface BudgetReading {
  spent: number;
  reserved: number;
  budget: number;
  /** Spent plus reserved, over the budget: what the reservation rule compares. */
  ratio: number;
  tone: BudgetTone;
}

/**
 * The month's reading, or null for an evaluator without a budget or an unreadable figure.
 * Reservations of running calls count as used, as the server's `remaining_usd` does.
 */
export function readBudget(status: EvaluatorBudget | EvaluatorBudgetSummary): BudgetReading | null {
  const budget = parseMoney(status.monthly_budget_usd);
  const spent = parseMoney(status.spent_usd);
  const reserved = parseMoney(status.reserved_usd);
  if (budget === null || budget <= 0 || spent === null || reserved === null) {
    return null;
  }
  const ratio = (spent + reserved) / budget;
  const remaining = "remaining_usd" in status ? parseMoney(status.remaining_usd) : null;
  const tone: BudgetTone =
    ratio >= 1 || (remaining !== null && remaining <= 0)
      ? "spent"
      : ratio >= WARNING_RATIO
        ? "warning"
        : "normal";
  return { spent, reserved, budget, ratio, tone };
}

/** The bar's filled share, 0 to 1. */
export function barShare(ratio: number): number {
  return Math.min(Math.max(ratio, 0), 1);
}

/** "82%", rounded down so "100%" means the budget is used up (99.6 % reads "99%"). */
export function usedPercent(ratio: number): string {
  // The epsilon keeps a float like 0.29 * 100 = 28.999… from flooring to 28.
  return `${Math.floor(ratio * 100 + 1e-9)}%`;
}

/** "October 2026" for the UTC month the budget covers. */
export function monthLabel(monthStart: string): string | null {
  const date = new Date(monthStart);
  if (Number.isNaN(date.getTime())) {
    return null;
  }
  return new Intl.DateTimeFormat("en-US", {
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  }).format(date);
}

/** "$0.31", or 4 decimals below a cent ("$0.0040"), as the Figma budget figures read. */
export function formatBudgetUsd(amount: number): string {
  return amount !== 0 && Math.abs(amount) < 0.01
    ? `$${amount.toFixed(4)}`
    : `$${amount.toFixed(2)}`;
}
