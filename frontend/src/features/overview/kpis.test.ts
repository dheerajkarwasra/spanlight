import { describe, expect, it } from "vitest";

import type { Kpis } from "@/lib/api";

import type { ChartPoint } from "./chart-data";
import {
  buildKpiCards,
  formatDeltaMagnitude,
  kpiTrend,
  msDelta,
  percentDelta,
  pointsDelta,
  previousPeriodLabel,
  type KpiCardModel,
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

function card(cards: KpiCardModel[], id: KpiCardModel["id"]): KpiCardModel {
  const found = cards.find((candidate) => candidate.id === id);
  if (!found) {
    throw new Error(`missing card ${id}`);
  }
  return found;
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

describe("buildKpiCards", () => {
  it("returns the four cards in display order", () => {
    const cards = buildKpiCards({ current: kpis(), previous: kpis(), approximate: false });
    expect(cards.map((item) => item.id)).toEqual(["p95", "error_rate", "llm_calls", "tokens"]);
  });

  it("formats values and detail lines", () => {
    const cards = buildKpiCards({
      current: kpis({ traces: 12_900, llm_calls: 48_200, error_rate: 203 / 48_200 }),
      previous: kpis(),
      approximate: false,
    });
    expect(card(cards, "p95").value).toBe("1.20 s");
    expect(card(cards, "p95").detail).toBe("p50 400 ms");
    expect(card(cards, "error_rate").detail).toBe("203 of 48.2K calls");
    expect(card(cards, "llm_calls").value).toBe("48.2K");
    expect(card(cards, "llm_calls").detail).toBe("across 12.9K traces");
    expect(card(cards, "tokens").value).toBe("12K");
    expect(card(cards, "tokens").detail).toBe("10K in · 2,000 out");
  });

  it("uses the right unit and polarity for each delta", () => {
    const cards = buildKpiCards({
      current: kpis({ p95_ms: 1420, error_rate: 0.04, llm_calls: 220 }),
      previous: kpis({ p95_ms: 1200, error_rate: 0.05, llm_calls: 200 }),
      approximate: false,
    });
    expect(card(cards, "p95").delta).toEqual({ value: 220, unit: "ms", increaseIsGood: false });
    expect(card(cards, "error_rate").delta).toEqual({
      value: -1,
      unit: "points",
      increaseIsGood: false,
    });
    expect(card(cards, "llm_calls").delta).toEqual({
      value: 10,
      unit: "percent",
      increaseIsGood: null,
    });
  });

  it("leaves latency and error rate unknown without LLM calls", () => {
    const cards = buildKpiCards({
      current: kpis({ llm_calls: 0, error_rate: null, p50_ms: null, p95_ms: null }),
      previous: kpis(),
      approximate: false,
    });
    expect(card(cards, "p95").value).toBeNull();
    expect(card(cards, "p95").detail).toBeNull();
    expect(card(cards, "p95").delta).toBeNull();
    expect(card(cards, "error_rate").value).toBeNull();
    expect(card(cards, "error_rate").detail).toBeNull();
  });

  it("uses singular nouns for one call or one trace", () => {
    const cards = buildKpiCards({
      current: kpis({ traces: 1, llm_calls: 1, error_rate: 0 }),
      previous: kpis(),
      approximate: false,
    });
    expect(card(cards, "llm_calls").detail).toBe("across 1 trace");
    expect(card(cards, "error_rate").detail).toBe("0 of 1 call");
  });
});

describe("kpiTrend", () => {
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
    {
      bucketStart: "b",
      llmCalls: 0,
      errors: 0,
      succeeded: 0,
      partial: false,
      p95Ms: null,
      costUsd: null,
      tokens: 0,
    },
  ];

  it("maps each card to its per-bucket series", () => {
    expect(kpiTrend("p95", points)).toEqual([900, null]);
    expect(kpiTrend("error_rate", points)).toEqual([0.25, null]);
    expect(kpiTrend("llm_calls", points)).toEqual([4, 0]);
    expect(kpiTrend("tokens", points)).toEqual([300, 0]);
  });

  it("covers every bucket of the window", () => {
    const many: ChartPoint[] = Array.from({ length: 24 }, (_, index) => ({
      bucketStart: String(index),
      llmCalls: index,
      errors: 0,
      succeeded: index,
      partial: false,
      p95Ms: null,
      costUsd: null,
      tokens: 0,
    }));
    expect(kpiTrend("llm_calls", many)).toHaveLength(24);
  });
});

describe("previousPeriodLabel", () => {
  it("describes the comparison window", () => {
    expect(previousPeriodLabel({ value: "24h" })).toBe("previous 24h");
    expect(previousPeriodLabel({ value: "custom" })).toBe("previous period");
  });
});
