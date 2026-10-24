import { describe, expect, it } from "vitest";

import type { Health, Kpis } from "@/lib/api";

import type { ChartPoint } from "./chart-data";
import {
  formatDeltaMagnitude,
  msDelta,
  percentDelta,
  pointsDelta,
  previousPeriodLabel,
  ribbonKpis,
  type RibbonKpi,
} from "./kpis";

function kpis(overrides: Partial<Kpis> = {}): Kpis {
  return {
    traces: 100,
    llm_calls: 200,
    error_rate: 0.05,
    p50_ms: 400,
    p95_ms: 1200,
    cost_usd: "1.50",
    unpriced_calls: 0,
    input_tokens: 10_000,
    output_tokens: 2_000,
    ...overrides,
  };
}

describe("deltas", () => {
  it("expresses relative change in percent, rounded to one decimal", () => {
    expect(percentDelta(112.4, 100)).toBe(12.4);
    expect(percentDelta(96, 100)).toBe(-4);
  });

  it("has no relative change against an empty or unknown previous window", () => {
    expect(percentDelta(10, 0)).toBeNull();
    expect(percentDelta(10, null)).toBeNull();
  });

  it("reads a change that rounds away as flat, never −0", () => {
    expect(Object.is(percentDelta(99.99, 100), 0)).toBe(true);
  });

  it("measures rates in percentage points", () => {
    expect(pointsDelta(0.0042, 0.0052)).toBe(-0.1);
    expect(pointsDelta(null, 0.01)).toBeNull();
  });

  it("measures latency in whole milliseconds", () => {
    expect(msDelta(1840, 1620)).toBe(220);
    expect(msDelta(1840, null)).toBeNull();
  });

  it("formats magnitudes per unit", () => {
    expect(formatDeltaMagnitude("percent", 12.4)).toBe("12.4%");
    expect(formatDeltaMagnitude("points", 0.1)).toBe("0.1 pt");
    expect(formatDeltaMagnitude("ms", 220)).toBe("220 ms");
    expect(formatDeltaMagnitude("ms", 1500)).toBe("1.50 s");
  });
});

describe("ribbonKpis", () => {
  const range = { value: "24h", durationMs: 24 * 3600_000 } as const;
  const now = new Date("2026-10-21T12:00:00Z");
  const points: ChartPoint[] = [
    {
      bucketStart: "a",
      llmCalls: 4,
      errors: 1,
      succeeded: 3,
      partial: true,
      p95Ms: 900,
      costUsd: 0.1,
      tokens: 300,
    },
  ];

  function ribbon(overrides: Partial<Kpis> = {}, previous: Partial<Kpis> = {}) {
    return ribbonKpis({
      metrics: { current: kpis(overrides), previous: kpis(previous), approximate: true },
      points,
      health: { value: 72, open_critical: 1, open_warning: 2, approximate: false } as Health,
      range,
      now,
    });
  }

  function find(cards: RibbonKpi[], id: RibbonKpi["id"]): RibbonKpi {
    const found = cards.find((candidate) => candidate.id === id);
    if (!found) {
      throw new Error(`missing card ${id}`);
    }
    return found;
  }

  it("returns calls, p95, spend and Doctor in display order", () => {
    expect(ribbon().map((item) => item.id)).toEqual(["calls", "p95", "spend", "doctor"]);
  });

  it("counts calls with their errors and rate", () => {
    const calls = find(
      ribbon({ llm_calls: 432, error_rate: 15 / 432 }, { llm_calls: 400 }),
      "calls",
    );
    expect(calls).toMatchObject({
      label: "Calls · error rate",
      value: "432",
      denominator: "15 errors · 3.5%",
      delta: { value: 8, unit: "percent", increaseIsGood: null },
      trend: [4],
    });
  });

  it("marks p95 and its p50 as estimates from rollups", () => {
    const p95 = find(ribbon(), "p95");
    expect(p95).toMatchObject({ value: "1.20 s", approximate: true, denominator: "p50 ≈ 400 ms" });
    expect(p95.trend).toEqual([900]);
  });

  it("projects spend for short windows and flags a lower bound", () => {
    const spend = find(ribbon({ cost_usd: "1.00", unpriced_calls: 3 }), "spend");
    expect(spend).toMatchObject({ value: "$1.00", lowerBound: true, trend: null });
    expect(spend.denominator).toBe("Projected $31.00 this month");
  });

  it("leaves spend unknown when no call was priced", () => {
    const spend = find(ribbon({ cost_usd: null }), "spend");
    expect(spend.value).toBeNull();
    expect(spend.lowerBound).toBe(false);
  });

  it("scores the Doctor with its open findings", () => {
    expect(find(ribbon(), "doctor")).toMatchObject({
      value: "72",
      denominator: "3 open findings",
      critical: 1,
    });
  });

  it("keeps every value short enough for a phone tile, the exact spend aside", () => {
    const cards = ribbon({
      llm_calls: 1_234_567,
      cost_usd: "12345.678901",
      p95_ms: 123_456,
    });
    for (const card of cards) {
      expect(card.value?.length ?? 0).toBeLessThanOrEqual(8);
    }
    expect(find(cards, "spend")).toMatchObject({ value: "$12.3K", exact: "$12,345.68" });
    expect(find(ribbon({ cost_usd: "0.000662" }), "spend").value).toBe("<$0.01");
  });
});

describe("previousPeriodLabel", () => {
  it("describes the comparison window", () => {
    expect(previousPeriodLabel({ value: "24h" })).toBe("previous 24h");
    expect(previousPeriodLabel({ value: "custom" })).toBe("previous period");
  });
});
