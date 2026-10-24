import { describe, expect, it } from "vitest";

import type { AttributionSeries } from "@/lib/api";

import { barLines, barSummary, seriesBars, spendTick } from "./spend-series";

function series(overrides: Partial<AttributionSeries> = {}): AttributionSeries {
  return {
    bucket: "day",
    source: "rollup",
    window: { from: "2026-10-14T00:00:00Z", to: "2026-10-16T00:00:00Z" },
    points: [
      { bucket_start: "2026-10-14T00:00:00Z", cost_usd: "4.12000000", unpriced_calls: 0 },
      { bucket_start: "2026-10-15T00:00:00Z", cost_usd: "1.50000000", unpriced_calls: 3 },
      { bucket_start: "2026-10-16T00:00:00Z", cost_usd: null, unpriced_calls: 7 },
    ],
    ...overrides,
  };
}

describe("seriesBars", () => {
  it("labels UTC days and keeps an unknown cost null, never 0", () => {
    expect(seriesBars(series())).toEqual([
      {
        start: "2026-10-14T00:00:00Z",
        label: "Oct 14",
        cost: 4.12,
        lowerBound: false,
        unpriced: 0,
      },
      { start: "2026-10-15T00:00:00Z", label: "Oct 15", cost: 1.5, lowerBound: true, unpriced: 3 },
      {
        start: "2026-10-16T00:00:00Z",
        label: "Oct 16",
        cost: null,
        lowerBound: false,
        unpriced: 7,
      },
    ]);
  });

  it("keeps a bucket without traces at 0", () => {
    const [bar] = seriesBars(
      series({
        points: [{ bucket_start: "2026-10-14T00:00:00Z", cost_usd: "0", unpriced_calls: 0 }],
      }),
    );
    expect(bar?.cost).toBe(0);
  });

  it("labels hours with the time, and with the day once the bars pass one day", () => {
    const hour = (iso: string) => ({ bucket_start: iso, cost_usd: "1", unpriced_calls: 0 });
    const short = seriesBars(series({ bucket: "hour", points: [hour("2026-10-14T13:00:00Z")] }));
    expect(short[0]?.label).toMatch(/^\d\d:\d\d$/);
    const points = Array.from({ length: 25 }, (_, index) =>
      hour(new Date(Date.UTC(2026, 9, 14, index)).toISOString()),
    );
    expect(seriesBars(series({ bucket: "hour", points }))[0]?.label).toMatch(
      /^Oct 1\d, \d\d:\d\d$/,
    );
  });
});

describe("barSummary", () => {
  it("reads like the spec's tooltip", () => {
    expect(seriesBars(series()).map(barSummary)).toEqual([
      "Oct 14 · $4.12 · 0 unpriced calls",
      "Oct 15 · $1.50 · 3 unpriced calls",
      "Oct 16 · No priced calls · 7 unpriced calls",
    ]);
  });
});

describe("barLines", () => {
  const [priced, lowerBound] = seriesBars(series());

  it("adds Lower bound to a bar with unpriced calls", () => {
    expect(priced && barLines(priced, false, null)).toEqual(["Oct 14 · $4.12 · 0 unpriced calls"]);
    expect(lowerBound && barLines(lowerBound, false, null)).toEqual([
      "Oct 15 · $1.50 · 3 unpriced calls",
      "Lower bound",
    ]);
  });

  it("says on the last bar only that today's traces aren't all counted yet", () => {
    const note = "Some of today's traces aren't counted yet";
    expect(priced && barLines(priced, true, 12)).toContain(note);
    expect(priced && barLines(priced, false, 12)).not.toContain(note);
    expect(priced && barLines(priced, true, 0)).not.toContain(note);
    expect(priced && barLines(priced, true, null)).not.toContain(note);
  });
});

describe("spendTick", () => {
  it("keeps cents from a dime up, sub-dime digits below, and compacts from $1k", () => {
    expect([0.8, 0.1, 12.5, 999, 0.05, 0.002, 1500].map(spendTick)).toEqual([
      "$0.80",
      "$0.10",
      "$12.50",
      "$999.00",
      "$0.0500",
      "$0.002000",
      "$1,500",
    ]);
  });
});
