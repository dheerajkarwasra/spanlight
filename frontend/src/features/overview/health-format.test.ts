import { describe, expect, it } from "vitest";

import type { Health } from "@/lib/api";

import {
  componentDetail,
  formatComponentValue,
  healthBand,
  penaltyRows,
  ringFraction,
  summaryLine,
} from "./health-format";

function health(overrides: Partial<Health>): Health {
  return {
    value: 90,
    components: [],
    open_critical: 0,
    open_warning: 0,
    window: { start: "2026-10-12T00:00:00Z", end: "2026-10-13T00:00:00Z" },
    approximate: false,
    ...overrides,
  };
}

describe("healthBand", () => {
  it("draws the bands at 80 and 50", () => {
    expect(healthBand(100)).toBe("good");
    expect(healthBand(80)).toBe("good");
    expect(healthBand(79)).toBe("fair");
    expect(healthBand(50)).toBe("fair");
    expect(healthBand(49)).toBe("poor");
    expect(healthBand(0)).toBe("poor");
  });

  it("has no band (not 'poor') when there is no score", () => {
    expect(healthBand(null)).toBe("none");
    expect(ringFraction(null)).toBe(0);
  });
});

describe("component values", () => {
  it("shows each component in its own unit", () => {
    expect(formatComponentValue("errors", "0.125")).toBe("12.5%");
    expect(formatComponentValue("latency", "1840")).toBe("1.84 s");
    expect(formatComponentValue("cost", "0.5")).toMatch(/^\$0\.5/);
    expect(formatComponentValue("findings", "2")).toBe("2");
  });

  it("is null for an unknown value, never 0", () => {
    expect(formatComponentValue("errors", null)).toBeNull();
    expect(formatComponentValue("latency", "")).toBeNull();
    expect(formatComponentValue("cost", "n/a")).toBeNull();
  });

  it("drops the baseline when it is unknown", () => {
    expect(
      componentDetail({ name: "latency", penalty: 10, observed: "1840", baseline: null }),
    ).toBe("1.84 s");
    expect(
      componentDetail({ name: "latency", penalty: 10, observed: "1840", baseline: "1620" }),
    ).toBe("1.84 s vs 1.62 s baseline");
  });
});

describe("penaltyRows", () => {
  it("lists only components that took points off, with a true minus sign", () => {
    const rows = penaltyRows([
      { name: "errors", penalty: 20, observed: "0.2", baseline: "0.01" },
      { name: "cost", penalty: 0, observed: "1", baseline: "1" },
    ]);
    expect(rows).toEqual([
      { name: "errors", label: "Errors", penalty: "−20", detail: "20.0% vs 1.0% baseline" },
    ]);
  });
});

describe("summaryLine", () => {
  it("keeps real findings visible when the window had no LLM calls", () => {
    expect(
      summaryLine(health({ value: null, open_critical: 1, open_warning: 1 }), "last 24h"),
    ).toBe("No LLM calls · 2 open findings (1 critical, 1 warning) · last 24h");
  });

  it("says there are no findings only when there is a score", () => {
    expect(summaryLine(health({}), "last 7d")).toBe("No open findings · last 7d");
    expect(summaryLine(health({ value: null }), "last 7d")).toBe("No LLM calls · last 7d");
  });
});
