import { describe, expect, it } from "vitest";

import { bulletFor, bulletPositions, bulletSentence } from "./evidence-bullet";

const WINDOW_15 = { start: "2026-10-21T13:47:00Z", end: "2026-10-21T14:02:00Z" };
const WINDOW_60 = { start: "2026-10-21T13:00:00Z", end: "2026-10-21T14:00:00Z" };

function insight(
  kind: string,
  metrics: Record<string, string | number | null>,
  window = WINDOW_15,
) {
  return { kind, evidence: { trace_ids: [], metrics, window } };
}

describe("bulletFor", () => {
  it("reads the error rate against its baseline for an error spike", () => {
    const bullet = bulletFor(
      insight("error_spike", { error_rate: "0.1820", baseline_error_rate: "0.0210", calls: 1204 }),
    );
    expect(bullet).toEqual({
      label: "Error rate",
      observed: 0.182,
      baseline: 0.021,
      unit: "pct",
      windowLabel: "15 min window",
    });
  });

  it("reads p95 against its baseline for a latency regression", () => {
    expect(
      bulletFor(
        insight("latency_regression", { p95_ms: 4200, baseline_p95_ms: "1800" }, WINDOW_60),
      ),
    ).toEqual({
      label: "p95 latency",
      observed: 4200,
      baseline: 1800,
      unit: "ms",
      windowLabel: "60 min window",
    });
  });

  it("reads spend against the hourly baseline for a cost spike", () => {
    expect(
      bulletFor(
        insight("cost_spike", { spend_usd: "12.50", baseline_hourly_usd: "2.10" }, WINDOW_60),
      ),
    ).toMatchObject({ label: "Spend", observed: 12.5, baseline: 2.1, unit: "usd" });
  });

  it("has no chart for kinds without a baseline", () => {
    expect(bulletFor(insight("rate_limit_pressure", { share: "0.4", calls: 10 }))).toBeNull();
    expect(bulletFor(insight("made_up_kind", { error_rate: "0.1" }))).toBeNull();
  });

  it("has no chart when either number is missing or unreadable", () => {
    expect(bulletFor(insight("error_spike", { error_rate: "0.1" }))).toBeNull();
    expect(
      bulletFor(insight("error_spike", { error_rate: null, baseline_error_rate: "0.01" })),
    ).toBeNull();
    expect(
      bulletFor(insight("error_spike", { error_rate: "NaN", baseline_error_rate: "0.01" })),
    ).toBeNull();
    expect(
      bulletFor(insight("error_spike", { error_rate: "", baseline_error_rate: "0.01" })),
    ).toBeNull();
    expect(
      bulletFor(insight("cost_spike", { spend_usd: "Infinity", baseline_hourly_usd: "1" })),
    ).toBeNull();
  });
});

describe("bulletSentence", () => {
  it("names both values, the unit and the window", () => {
    const bullet = bulletFor(
      insight("error_spike", { error_rate: "0.182", baseline_error_rate: "0.021" }),
    );
    expect(bullet && bulletSentence(bullet)).toBe(
      "Error rate 18.2% vs baseline 2.1% from the 7 days before the last 24 hours · 15 min window",
    );
  });

  it("formats durations and money", () => {
    const latency = bulletFor(
      insight("latency_regression", { p95_ms: 4200, baseline_p95_ms: 800 }, WINDOW_60),
    );
    expect(latency && bulletSentence(latency)).toBe(
      "p95 latency 4.20 s vs baseline 800 ms from the 7 days before the last 24 hours · 60 min window",
    );
    const cost = bulletFor(
      insight("cost_spike", { spend_usd: "12.5", baseline_hourly_usd: "2.1" }, WINDOW_60),
    );
    expect(cost && bulletSentence(cost)).toBe(
      "Spend $12.50 vs baseline $2.10 from the 7 days before the last 24 hours · 60 min window",
    );
  });
});

describe("bulletPositions", () => {
  it("places both values on one scale with headroom past the larger one", () => {
    const { observed, baseline } = bulletPositions({ observed: 0.2, baseline: 0.05 });
    expect(observed).toBeCloseTo(80);
    expect(baseline).toBeCloseTo(20);
  });

  it("keeps a baseline above the observed value on the scale", () => {
    const { observed, baseline } = bulletPositions({ observed: 1, baseline: 4 });
    expect(baseline).toBeCloseTo(80);
    expect(observed).toBeCloseTo(20);
  });

  it("draws nothing for two zeros instead of dividing by zero", () => {
    expect(bulletPositions({ observed: 0, baseline: 0 })).toEqual({ observed: 0, baseline: 0 });
  });
});
