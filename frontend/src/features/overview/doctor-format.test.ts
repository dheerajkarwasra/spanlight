import { describe, expect, it } from "vitest";

import type { Health, Kpis } from "@/lib/api";

import {
  componentRows,
  formatComponentValue,
  formatPoints,
  healthBand,
  ringFraction,
} from "./doctor-format";

const METRICS: Kpis = {
  traces: 51,
  llm_calls: 99,
  error_rate: 0,
  p50_ms: 910,
  p95_ms: 1290,
  cost_usd: "0.0204",
  unpriced_calls: 0,
  input_tokens: 0,
  output_tokens: 0,
};

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

  it("says when there is no previous window to compare with", () => {
    const rows = componentRows(
      health({ components: [{ name: "latency", penalty: 10, observed: "1840", baseline: null }] }),
      METRICS,
      true,
    );
    expect(rows[2]).toMatchObject({ value: "p95 1.84 s", note: "no previous window" });
  });
});

describe("formatPoints", () => {
  it("shows points off the cap with a true minus sign", () => {
    expect(formatPoints(20, 30)).toBe("−20 of 30");
    expect(formatPoints(0, 40)).toBe("0 of 40");
  });
});

describe("componentRows", () => {
  it("keeps real findings visible when the window had no LLM calls", () => {
    const rows = componentRows(
      health({
        value: null,
        open_critical: 1,
        open_warning: 1,
        components: [{ name: "findings", penalty: 20, observed: "2", baseline: null }],
      }),
      METRICS,
      true,
    );
    expect(rows[0]).toMatchObject({ value: "2 open", note: "1 critical, 1 warning", penalty: 20 });
    expect(rows[1]).toMatchObject({ value: null, penalty: null });
  });

  it("leaves findings unknown before the first detector pass", () => {
    expect(componentRows(health({}), METRICS, false)[0]).toMatchObject({
      value: "No detector pass yet",
      penalty: null,
    });
  });
});
