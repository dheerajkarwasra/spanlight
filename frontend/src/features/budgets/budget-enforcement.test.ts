import { describe, expect, it } from "vitest";

import { softLimitBadgeTooltip, softLimitHint, softLimitNote } from "./budget-enforcement";

// Spec §8.4, word for word: the dialog hint, the badge tooltip and the page note.
const HINT =
  "Soft limit. Gateway calls in this scope get 402 BUDGET_EXCEEDED after spend is measured above the amount. Spend is measured every 60 seconds, so calls keep going through until the next pass: in the 2026-10-14 pilot a $0.002 budget started blocking after 85.5 s and spend reached $0.0122 (6×). For a limit that must hold, set the amount below it. Traces sent with the SDK are never blocked.";

describe("softLimitHint", () => {
  it("is the spec's dialog copy, built from the measured numbers", () => {
    const hint = softLimitHint();
    for (const part of ["every 60 seconds", "85.5 s", "$0.0122 (6×)", "2026-10-14", "$0.002"]) {
      expect(hint).toContain(part);
    }
    expect(hint).toBe(HINT);
  });
});

describe("softLimitBadgeTooltip", () => {
  it("is the hint's first two sentences", () => {
    expect(softLimitBadgeTooltip()).toBe(
      "Soft limit. Gateway calls in this scope get 402 BUDGET_EXCEEDED after spend is measured above the amount.",
    );
    expect(HINT.startsWith(softLimitBadgeTooltip())).toBe(true);
  });
});

describe("softLimitNote", () => {
  it("is the budgets page note", () => {
    expect(softLimitNote()).toBe(
      "Blocking is a soft limit: spend is measured every 60 s. Nothing sent with the SDK is ever blocked.",
    );
  });
});
