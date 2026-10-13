import { describe, expect, it } from "vitest";

import type { Comparison, ReleaseStats } from "@/lib/api";

import {
  NO_COST_REASON,
  buildKpiRows,
  formatRelativeMagnitude,
  formatShare,
} from "./release-deltas";

function stats(overrides: Partial<ReleaseStats>): ReleaseStats {
  return {
    release: "v1",
    first_seen_at: "2026-10-01T00:00:00Z",
    last_seen_at: "2026-10-02T00:00:00Z",
    traces: 100,
    llm_calls: 200,
    unpriced_calls: 0,
    error_rate: 0.01,
    p50_ms: 400,
    p95_ms: 1200,
    cost_usd: "1.50",
    input_tokens: 10_000,
    output_tokens: 2_000,
    ...overrides,
  };
}

function comparison(overrides: Partial<Comparison>): Comparison {
  return {
    a: stats({}),
    b: stats({ release: "v2" }),
    deltas: {},
    model_mix: [],
    error_classes: [],
    new_errors: [],
    ...overrides,
  };
}

function row(rows: ReturnType<typeof buildKpiRows>, key: string) {
  const found = rows.find((candidate) => candidate.key === key);
  if (!found) throw new Error(`no row ${key}`);
  return found;
}

describe("buildKpiRows", () => {
  it("keeps a release without LLM calls unknown, not 0 %", () => {
    const rows = buildKpiRows(
      comparison({ b: stats({ release: "v2", llm_calls: 0, error_rate: null, p95_ms: null }) }),
    );
    expect(row(rows, "error_rate").b).toBeNull();
    expect(row(rows, "error_rate").unknownReason).toBe(
      "This release made no LLM calls in the window.",
    );
    expect(row(rows, "p95_ms").delta).toBeNull();
    expect(row(rows, "p95_ms").changeMagnitude).toBeNull();
  });

  it("keeps an unpriced release's cost unknown and explains why", () => {
    const rows = buildKpiRows(comparison({ a: stats({ cost_usd: null, unpriced_calls: 200 }) }));
    const cost = row(rows, "cost_usd");
    expect(cost.a).toBeNull();
    expect(cost.aCost).toBeNull();
    expect(cost.unknownReason).toBe(NO_COST_REASON);
  });

  it("marks a partly priced release's cost as a lower bound", () => {
    const rows = buildKpiRows(comparison({ b: stats({ release: "v2", unpriced_calls: 3 }) }));
    expect(row(rows, "cost_usd").aLowerBound).toBe(false);
    expect(row(rows, "cost_usd").bLowerBound).toBe(true);
  });

  it("states an error-rate change in percentage points, signed for tone", () => {
    const rows = buildKpiRows(
      comparison({ deltas: { error_rate: { absolute: "-0.0089", relative: "-0.5" } } }),
    );
    const errors = row(rows, "error_rate");
    expect(errors.delta).toBeCloseTo(-0.0089);
    expect(errors.changeMagnitude).toBe("0.89 pt");
    expect(errors.relativeMagnitude).toBe("50 %");
  });

  it("has no relative change when the baseline is 0", () => {
    const rows = buildKpiRows(
      comparison({ deltas: { traces: { absolute: "40", relative: null } } }),
    );
    expect(row(rows, "traces").changeMagnitude).toBe("40");
    expect(row(rows, "traces").relativeMagnitude).toBeNull();
  });
});

describe("formatRelativeMagnitude", () => {
  it("never rounds a real change down to 0 %", () => {
    expect(formatRelativeMagnitude("0.004")).toBe("<1 %");
    expect(formatRelativeMagnitude("0")).toBe("0 %");
    expect(formatRelativeMagnitude("0.028")).toBe("2.8 %");
    expect(formatRelativeMagnitude("-1.234")).toBe("123 %");
    expect(formatRelativeMagnitude(null)).toBeNull();
  });
});

describe("formatShare", () => {
  it("is null without calls", () => {
    expect(formatShare(null)).toBeNull();
    expect(formatShare(0.336)).toBe("34 %");
  });
});
