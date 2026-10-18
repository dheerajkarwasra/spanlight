import { describe, expect, it } from "vitest";

import type { ModelMetrics, TimeseriesPoint } from "@/lib/api";

import {
  formatBucketRange,
  formatBucketTick,
  hasCallData,
  hasCostData,
  summarizeCalls,
  toChartPoints,
} from "./chart-data";
import {
  formatProvider,
  isUnpriced,
  modelErrorRate,
  sortModelsByCalls,
  unpricedCalls,
} from "./models";

function point(overrides: Partial<TimeseriesPoint> = {}): TimeseriesPoint {
  return {
    bucket_start: "2026-10-07T14:00:00Z",
    llm_calls: 10,
    errors: 2,
    p95_ms: 900,
    cost_usd: "0.25",
    tokens: 500,
    approximate: false,
    ...overrides,
  };
}

/** A 24 h window that holds the 14:00 bucket whole. */
const RANGE = {
  from: "2026-10-07T00:00:00Z",
  to: "2026-10-08T00:00:00Z",
  durationMs: 24 * 3600_000,
};

describe("toChartPoints", () => {
  it("parses money and keeps the counts", () => {
    expect(toChartPoints([point()], RANGE)).toEqual([
      {
        bucketStart: "2026-10-07T14:00:00Z",
        llmCalls: 10,
        errors: 2,
        succeeded: 8,
        partial: false,
        p95Ms: 900,
        costUsd: 0.25,
        tokens: 500,
      },
    ]);
  });

  it("marks the buckets the window clips or that are still filling as partial", () => {
    const points = toChartPoints(
      [
        point({ bucket_start: "2026-10-06T23:00:00Z" }),
        point({ bucket_start: "2026-10-07T23:00:00Z" }),
      ],
      { ...RANGE, from: "2026-10-06T23:30:00Z", to: "2026-10-07T23:30:00Z" },
    );
    expect(points.map((chartPoint) => chartPoint.partial)).toEqual([true, true]);
  });

  it("keeps unknown values null so charts draw gaps", () => {
    const [chartPoint] = toChartPoints([point({ p95_ms: null, cost_usd: null })], RANGE);
    expect(chartPoint?.p95Ms).toBeNull();
    expect(chartPoint?.costUsd).toBeNull();
  });
});

describe("summarizeCalls", () => {
  it("totals calls and errors", () => {
    const points = toChartPoints(
      [
        point({ llm_calls: 5, errors: 1 }),
        point({ llm_calls: 0, errors: 0 }),
        point({ llm_calls: 7, errors: 0 }),
      ],
      RANGE,
    );
    expect(summarizeCalls(points)).toEqual({ calls: 12, errors: 1 });
  });
});

describe("emptiness checks", () => {
  it("treats all-zero series as empty", () => {
    const zeros = toChartPoints(
      [point({ llm_calls: 0, errors: 0, p95_ms: null, cost_usd: "0" })],
      RANGE,
    );
    expect(hasCallData(zeros)).toBe(false);
    expect(hasCostData(zeros)).toBe(false);
  });

  it("detects real data", () => {
    const data = toChartPoints([point()], RANGE);
    expect(hasCallData(data)).toBe(true);
    expect(hasCostData(data)).toBe(true);
  });
});

describe("formatBucketTick", () => {
  it("formats daily buckets as a UTC calendar date", () => {
    expect(formatBucketTick("2026-10-07T00:00:00Z", "day")).toBe("Oct 7");
  });

  it("formats hourly buckets as a 24h clock time", () => {
    expect(formatBucketTick("2026-10-07T14:00:00Z", "hour")).toMatch(/^\d{2}:00$/);
  });

  it("returns an empty string for invalid dates", () => {
    expect(formatBucketTick("not a date", "hour")).toBe("");
  });
});

describe("formatBucketRange", () => {
  // Local times, so the test holds in any time zone.
  const now = new Date(2026, 9, 8, 14, 30);

  it("labels today's hourly buckets with the hour range", () => {
    const start = new Date(2026, 9, 8, 10, 0).toISOString();
    expect(formatBucketRange(start, "hour", now)).toBe("Today · 10:00 – 11:00");
  });

  it("labels yesterday's hourly buckets", () => {
    const start = new Date(2026, 9, 7, 23, 0).toISOString();
    expect(formatBucketRange(start, "hour", now)).toBe("Yesterday · 23:00 – 00:00");
  });

  it("falls back to the date for older hourly buckets", () => {
    const start = new Date(2026, 9, 5, 9, 0).toISOString();
    expect(formatBucketRange(start, "hour", now)).toBe("Oct 5 · 09:00 – 10:00");
  });

  it("names daily buckets relative to today (UTC)", () => {
    const utcNow = new Date("2026-10-08T12:00:00Z");
    expect(formatBucketRange("2026-10-08T00:00:00Z", "day", utcNow)).toBe("Today");
    expect(formatBucketRange("2026-10-07T00:00:00Z", "day", utcNow)).toBe("Yesterday");
    expect(formatBucketRange("2026-10-05T00:00:00Z", "day", utcNow)).toBe("Mon, Oct 5");
  });
});

describe("models", () => {
  function model(overrides: Partial<ModelMetrics>): ModelMetrics {
    return {
      provider: "openai",
      model: "gpt-4o-mini",
      calls: 1,
      errors: 0,
      p50_ms: null,
      p95_ms: null,
      input_tokens: 0,
      output_tokens: 0,
      cost_usd: null,
      approximate: false,
      ...overrides,
    };
  }

  it("sorts by calls descending, then by name", () => {
    const sorted = sortModelsByCalls([
      model({ model: "b", calls: 5 }),
      model({ model: "a", calls: 5 }),
      model({ model: "c", calls: 9 }),
    ]);
    expect(sorted.map((item) => item.model)).toEqual(["c", "a", "b"]);
  });

  it("computes an error rate only when there were calls", () => {
    expect(modelErrorRate({ calls: 4, errors: 1 })).toBe(0.25);
    expect(modelErrorRate({ calls: 0, errors: 0 })).toBeNull();
  });

  it("names known providers and shows the rest as sent", () => {
    expect(formatProvider("openai")).toBe("OpenAI");
    expect(formatProvider("anthropic")).toBe("Anthropic");
    expect(formatProvider("acme")).toBe("acme");
    expect(formatProvider(null)).toBeNull();
    expect(formatProvider("  ")).toBeNull();
  });

  it("counts calls made with unpriced models", () => {
    const models = [
      model({ calls: 3, cost_usd: "0.10" }),
      model({ calls: 5, cost_usd: null }),
      model({ calls: 2, cost_usd: null }),
    ];
    expect(isUnpriced(models[0] ?? model({}))).toBe(false);
    expect(unpricedCalls(models)).toBe(7);
  });
});
