import { describe, expect, it } from "vitest";

import { historyLabelFor, statusFor } from "./page-state";
import type { OverviewQueries } from "./use-overview-queries";

/** Just the parts `statusFor` reads: 100 traces, `errors` of 100 calls failed, and the health query. */
function queries(errors: number, health: Partial<OverviewQueries["health"]>): OverviewQueries {
  return {
    overview: { data: { current: { traces: 100, llm_calls: 100, error_rate: errors / 100 } } },
    health,
  } as unknown as OverviewQueries;
}

describe("statusFor", () => {
  it("keeps the status line loading until the open findings are known", () => {
    expect(statusFor("ready", queries(0, { isPending: true, data: undefined }))).toBeNull();
  });

  it("falls back to the error-only sentence when the health check failed", () => {
    expect(
      statusFor("ready", queries(15, { isPending: false, isError: true, data: undefined })),
    ).toMatchObject({ lead: "15 errors", accent: "need a look." });
    expect(
      statusFor("ready", queries(0, { isPending: false, isError: true, data: undefined })),
    ).toMatchObject({
      kind: "health-unavailable",
      lead: "Doctor health is",
      accent: "unavailable.",
    });
  });

  it("does not read a cached score as current after a failed refresh", () => {
    const stale = { isPending: false, isError: true, data: { open_critical: 2, open_warning: 1 } };
    expect(statusFor("ready", queries(0, stale as Partial<OverviewQueries["health"]>))?.kind).toBe(
      "health-unavailable",
    );
    expect(
      statusFor("ready", queries(15, stale as Partial<OverviewQueries["health"]>)),
    ).toMatchObject({ lead: "15 errors", accent: "need a look." });
  });

  it("keeps a successful no-score response healthy, not unavailable", () => {
    const noLlm = {
      isPending: false,
      isError: false,
      data: { value: null, open_critical: 0, open_warning: 0 },
    };
    expect(statusFor("ready", queries(0, noLlm as Partial<OverviewQueries["health"]>))?.kind).toBe(
      "healthy",
    );
  });

  it("adds the open findings once health has loaded", () => {
    const health = { isPending: false, data: { open_critical: 1, open_warning: 0 } };
    expect(
      statusFor("ready", queries(15, health as Partial<OverviewQueries["health"]>)),
    ).toMatchObject({ lead: "15 errors and 1 finding", accent: "need a look." });
  });
});

describe("historyLabelFor", () => {
  const nullBucket = {
    bucket_start: "2026-11-01T00:00:00Z",
    llm_calls: null,
    errors: null,
    p95_ms: null,
    cost_usd: null,
    tokens: null,
    approximate: false,
  };
  function history(timeseriesItems: object[]): OverviewQueries {
    return {
      overview: {
        data: { current: { traces: null, llm_calls: null, aggregate_status: "unavailable" } },
      },
      timeseries: { data: { items: timeseriesItems, aggregate_status: "unavailable" } },
      models: { data: { items: [], aggregate_status: "unavailable" } },
    } as unknown as OverviewQueries;
  }

  it("does not count empty buckets as retained figures", () => {
    expect(historyLabelFor(history([nullBucket, nullBucket]))).toBe("Source history unavailable");
  });

  it("keeps the retained label when one bucket still has a figure", () => {
    expect(historyLabelFor(history([nullBucket, { ...nullBucket, llm_calls: 3 }]))).toBe(
      "Retained snapshot",
    );
  });
});
