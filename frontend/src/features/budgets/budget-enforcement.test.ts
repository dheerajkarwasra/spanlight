import { describe, expect, it } from "vitest";

import { blockHint, blockingBadge, enforcementNote } from "./budget-enforcement";

// Figma "Budgets — Hard admission (G-007)", word for word.

// What `guard.py` does: a firing budget refuses unpriced calls too, and the reserve estimate
// counts text only. The hints may not claim more than that.
const REFUSAL =
  "A gateway call that would take spend, plus calls in flight, past the amount gets 402 BUDGET_EXCEEDED before it reaches the provider. A call to a model without a price is not counted and is admitted unless the budget is already firing. The estimate counts text only, so images, files and audio are not held until the call settles.";
const RETENTION = "A monthly block budget needs at least 31 days of project retention.";

describe("blockHint", () => {
  it("calls a gateway-key budget a hard limit for priced calls, with no soft-limit or SDK caveat", () => {
    const hint = blockHint("gateway_key");
    expect(hint).toBe(`Hard limit for priced gateway calls. ${REFUSAL} ${RETENTION}`);
    expect(hint).not.toMatch(/soft|SDK|let through/i);
  });

  it("says SDK calls in a mixed scope are measured, not stopped, and points to provider caps", () => {
    for (const scope of ["project", "user", "model", "metadata", "trace_name"] as const) {
      expect(blockHint(scope)).toBe(
        `${REFUSAL} SDK calls in this scope are measured every 60 seconds, not stopped; to cap them, set a spend limit with your provider. ${RETENTION}`,
      );
      expect(blockHint(scope)).not.toMatch(/hard limit|let through/i);
    }
  });
});

describe("blockingBadge", () => {
  it("drops the soft label for a gateway-key scope", () => {
    expect(blockingBadge("gateway_key")).toEqual({
      label: "Blocking",
      tooltip:
        "Gateway calls in this scope get 402 BUDGET_EXCEEDED until the period resets or the amount is raised.",
    });
  });

  it("keeps the soft label where SDK spend counts", () => {
    expect(blockingBadge("project")).toEqual({
      label: "Blocking (soft)",
      tooltip:
        "Soft limit. Gateway calls in this scope get 402 BUDGET_EXCEEDED. SDK calls are measured every 60 s, not stopped.",
    });
  });
});

describe("enforcementNote", () => {
  it("is the budgets page note: no soft-limit claim, SDK measured, provider caps", () => {
    expect(enforcementNote()).toBe(
      "Block budgets refuse a gateway call that would take spend, plus calls in flight, past the amount. SDK calls are measured, not stopped; to cap them, set a spend limit with your provider.",
    );
  });
});
