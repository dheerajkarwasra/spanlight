import { describe, expect, it } from "vitest";

import type { GatewaySeries } from "@/lib/api";

import { barSummary, requestsBars } from "./requests-series";

/** A local wall-clock time as the API's ISO string, so labels read the same in any time zone. */
function at(day: number, hour: number, minute = 0): string {
  return new Date(2026, 9, day, hour, minute).toISOString();
}

describe("requestsBars", () => {
  it("splits each bucket into succeeded calls and errors stacked on top", () => {
    const series: GatewaySeries = {
      bucket: "hour",
      points: [
        { bucket_start: at(20, 14), requests: 132, errors: 3 },
        { bucket_start: at(20, 15), requests: 0, errors: 0 },
      ],
    };
    expect(requestsBars(series)).toEqual([
      { start: at(20, 14), label: "14:00", ok: 129, errors: 3 },
      { start: at(20, 15), label: "15:00", ok: 0, errors: 0 },
    ]);
  });

  it("labels 5-minute buckets by their start time", () => {
    const series: GatewaySeries = {
      bucket: "5m",
      points: [{ bucket_start: at(20, 14, 35), requests: 4, errors: 4 }],
    };
    expect(requestsBars(series)).toEqual([
      { start: at(20, 14, 35), label: "14:35", ok: 0, errors: 4 },
    ]);
  });

  it("names the day when the hours span more than one day", () => {
    const points = Array.from({ length: 168 }, (_, index) => ({
      bucket_start: new Date(2026, 9, 14, 9 + index).toISOString(),
      requests: 1,
      errors: 0,
    }));
    const bars = requestsBars({ bucket: "hour", points });
    expect(bars[0]?.label).toBe("Oct 14, 09:00");
    expect(bars.at(-1)?.label).toBe("Oct 21, 08:00");
  });
});

describe("barSummary", () => {
  it("reads as the tooltip line", () => {
    expect(barSummary({ start: at(20, 14), label: "14:00", ok: 129, errors: 3 })).toBe(
      "14:00 · 132 requests · 3 errors",
    );
    expect(barSummary({ start: at(20, 14), label: "14:00", ok: 1, errors: 0 })).toBe(
      "14:00 · 1 request · 0 errors",
    );
  });
});
