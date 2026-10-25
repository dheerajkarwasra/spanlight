import { describe, expect, it } from "vitest";

import type { Sensitivity } from "@/lib/api";

import {
  gateText,
  gateValue,
  isStricter,
  levelDescription,
  levelLabel,
  LEVELS,
} from "./sensitivity-format";

function item(overrides: Partial<Sensitivity> = {}): Sensitivity {
  return {
    detector: "error_spike",
    tunable: true,
    level: "high_recall",
    metric_label: "error rate",
    gates: { balanced: "0.10", high_precision: "0.25" },
    findings_30d: 12,
    updated_at: null,
    ...overrides,
  };
}

describe("sensitivity wording", () => {
  it("names and describes the three levels in order", () => {
    expect(LEVELS.map(levelLabel)).toEqual(["High recall", "Balanced", "High precision"]);
    expect(levelDescription("high_recall")).toBe("Catch every pattern; dismiss the wrong ones.");
    expect(levelDescription("high_precision")).toBe(
      "Only strong signals; may miss early problems.",
    );
    expect(levelDescription("balanced")).not.toBe("");
  });

  it("orders levels by strictness", () => {
    expect(isStricter("high_recall", "balanced")).toBe(true);
    expect(isStricter("balanced", "high_precision")).toBe(true);
    expect(isStricter("high_precision", "balanced")).toBe(false);
    expect(isStricter("balanced", "balanced")).toBe(false);
  });
});

describe("gateText", () => {
  it("calls High recall today's rule", () => {
    expect(gateText(item(), "high_recall")).toBe("Today’s rule (default)");
  });

  it("writes share and rate gates as percentages", () => {
    expect(gateText(item(), "high_precision")).toBe("error rate 25\u00a0% or more");
    expect(gateValue(item(), "balanced")).toBe("10\u00a0%");
    const share = item({
      detector: "rate_limit_pressure",
      metric_label: "rate-limited share of calls",
      gates: { balanced: "0.20", high_precision: "0.30" },
    });
    expect(gateText(share, "balanced")).toBe("rate-limited share of calls 20\u00a0% or more");
  });

  it("writes baseline ratios with ×", () => {
    const cost = item({
      detector: "cost_spike",
      metric_label: "spend vs baseline",
      gates: { balanced: "5", high_precision: "10" },
    });
    expect(gateText(cost, "balanced")).toBe("spend 5× baseline or more");
    expect(gateValue(cost, "high_precision")).toBe("10× baseline");
  });

  it("writes counts with thousands separators", () => {
    const context = item({
      detector: "context_growth",
      metric_label: "last call input tokens",
      gates: { balanced: "50000", high_precision: "100000" },
    });
    expect(gateText(context, "high_precision")).toBe("last call input tokens 100,000 or more");
    expect(gateValue(context, "balanced")).toBe("50,000 last call input tokens");
  });

  it("takes the unit from the check, not from words in its label", () => {
    // A count whose label says "rate" stays a count; a share reworded without "share" stays a %.
    const retries = item({
      detector: "retry_storm",
      metric_label: "retry rate burst",
      gates: { balanced: "10", high_precision: "20" },
    });
    expect(gateValue(retries, "balanced")).toBe("10 retry rate burst");
    const truncated = item({
      detector: "truncated_outputs",
      metric_label: "calls cut short",
      gates: { balanced: "0.20", high_precision: "0.30" },
    });
    expect(gateValue(truncated, "high_precision")).toBe("30 %");
  });

  it("says a fixed check has one setting", () => {
    const fixed = item({
      detector: "unpriced_spend",
      tunable: false,
      metric_label: null,
      gates: null,
    });
    expect(gateText(fixed, "high_recall")).toBe("One setting");
  });
});
