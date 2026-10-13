import { describe, expect, it } from "vitest";

import type { EvaluatorBudget } from "@/lib/api";

import { barShare, formatBudgetUsd, monthLabel, readBudget, usedPercent } from "./budget-math";

function budget(overrides: Partial<EvaluatorBudget>): EvaluatorBudget {
  return {
    monthly_budget_usd: "10.00",
    spent_usd: "0",
    reserved_usd: "0",
    remaining_usd: "10.00",
    month_start: "2026-10-01T00:00:00Z",
    ...overrides,
  };
}

describe("readBudget", () => {
  it("counts reservations as used and warns from 80 %", () => {
    expect(readBudget(budget({ spent_usd: "5", reserved_usd: "2.99" }))?.tone).toBe("normal");
    expect(readBudget(budget({ spent_usd: "5", reserved_usd: "3" }))).toMatchObject({
      spent: 5,
      reserved: 3,
      budget: 10,
      ratio: 0.8,
      tone: "warning",
    });
  });

  it("reads as spent at the budget, or when the server says nothing remains", () => {
    expect(readBudget(budget({ spent_usd: "10" }))?.tone).toBe("spent");
    expect(readBudget(budget({ spent_usd: "1", remaining_usd: "0" }))?.tone).toBe("spent");
  });

  it("stays unknown, never zero, without a budget or a readable figure", () => {
    expect(readBudget(budget({ monthly_budget_usd: null }))).toBeNull();
    expect(readBudget(budget({ monthly_budget_usd: "0" }))).toBeNull();
    expect(readBudget(budget({ spent_usd: "" }))).toBeNull();
    expect(readBudget(budget({ reserved_usd: "abc" }))).toBeNull();
  });

  it("reads a summary without remaining_usd from its ratio alone", () => {
    const summary = { monthly_budget_usd: "4", spent_usd: "1", reserved_usd: "0" };
    expect(readBudget(summary)).toMatchObject({ ratio: 0.25, tone: "normal" });
  });
});

describe("meter figures", () => {
  it("clamps the bar between empty and full", () => {
    expect(barShare(-0.2)).toBe(0);
    expect(barShare(0.42)).toBe(0.42);
    expect(barShare(1.7)).toBe(1);
  });

  it("formats the used share as a whole percent", () => {
    expect(usedPercent(0.82)).toBe("82%");
  });

  // Finding C-NEW-1: the meter rounded, so 99.6 % used read "100%" while the tone was still
  // "warning". It rounds down, like the gateway budget screen and the experiments compare view.
  it("never reads 100% before the budget is used up", () => {
    expect(usedPercent(0.996)).toBe("99%");
    expect(usedPercent(0.29)).toBe("29%");
    expect(usedPercent(1)).toBe("100%");
    expect(usedPercent(1.25)).toBe("125%");
  });

  it("names the UTC month and formats sub-cent amounts with four decimals", () => {
    expect(monthLabel("2026-10-01T00:00:00Z")).toBe("October 2026");
    expect(monthLabel("not a date")).toBeNull();
    expect(formatBudgetUsd(0.31)).toBe("$0.31");
    expect(formatBudgetUsd(0.004)).toBe("$0.0040");
    expect(formatBudgetUsd(0)).toBe("$0.00");
  });
});
