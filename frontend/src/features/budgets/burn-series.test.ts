import { describe, expect, it } from "vitest";

import type { BudgetBurn } from "@/lib/api";

import { burnPoints, burnTooltip, periodDays } from "./burn-series";

const BURN: BudgetBurn = {
  period_start: "2026-10-01T00:00:00Z",
  resets_at: "2026-11-01T00:00:00Z",
  amount_usd: "500.0000",
  points: [
    { day: "2026-10-01", spend_usd: null, cumulative_usd: null },
    { day: "2026-10-02", spend_usd: "12.5000", cumulative_usd: "12.5000" },
    { day: "2026-10-03", spend_usd: null, cumulative_usd: "12.5000" },
    { day: "2026-10-14", spend_usd: "169.5000", cumulative_usd: "182.0000" },
  ],
};

describe("burnPoints", () => {
  it("keeps unknown days null, never 0, and carries the amount", () => {
    expect(burnPoints(BURN)).toEqual([
      { day: "2026-10-01", cumulative: null, amount: 500 },
      { day: "2026-10-02", cumulative: 12.5, amount: 500 },
      { day: "2026-10-03", cumulative: 12.5, amount: 500 },
      { day: "2026-10-14", cumulative: 182, amount: 500 },
    ]);
  });
});

describe("burnTooltip", () => {
  it("reads day, spend to date, amount and share", () => {
    expect(burnTooltip({ day: "2026-10-14", cumulative: 182, amount: 500 })).toBe(
      "Oct 14 · $182 of $500 (36 %)",
    );
  });

  it("says nothing was measured for an unknown day", () => {
    expect(burnTooltip({ day: "2026-10-01", cumulative: null, amount: 500 })).toBe(
      "Oct 1 · — of $500",
    );
  });
});

describe("periodDays", () => {
  it("lists every UTC day of the period, so the axis spans it whole", () => {
    const days = periodDays(BURN);
    expect(days).toHaveLength(31);
    expect([days[0], days.at(-1)]).toEqual(["2026-10-01", "2026-10-31"]);
  });

  it("is one day for a daily budget", () => {
    expect(
      periodDays({
        ...BURN,
        period_start: "2026-10-14T00:00:00Z",
        resets_at: "2026-10-15T00:00:00Z",
      }),
    ).toEqual(["2026-10-14"]);
  });
});
