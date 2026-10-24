import { describe, expect, it } from "vitest";

import { bucketLabel, bucketWindow, fillBuckets, markMinHeight } from "./histogram";

describe("fillBuckets", () => {
  it("zero-fills the hours of the window the response left out", () => {
    const filled = fillBuckets(
      {
        bucket: "hour",
        buckets: [{ bucket_start: "2026-10-14T11:00:00Z", traces: 4, errors: 1 }],
      },
      { from: "2026-10-14T09:30:00.000Z", to: "2026-10-14T12:00:00.000Z" },
    );
    expect(filled).toEqual([
      { start: "2026-10-14T09:00:00.000Z", traces: 0, errors: 0 },
      { start: "2026-10-14T10:00:00.000Z", traces: 0, errors: 0 },
      { start: "2026-10-14T11:00:00.000Z", traces: 4, errors: 1 },
    ]);
  });

  it("zero-fills days from the UTC midnight holding the window's start", () => {
    const filled = fillBuckets(
      {
        bucket: "day",
        buckets: [
          { bucket_start: "2026-10-12T00:00:00Z", traces: 132, errors: 3 },
          { bucket_start: "2026-10-14T00:00:00Z", traces: 9, errors: 0 },
        ],
      },
      { from: "2026-10-12T08:00:00.000Z", to: "2026-10-14T08:00:00.000Z" },
    );
    expect(filled.map((b) => [b.start.slice(0, 10), b.traces, b.errors])).toEqual([
      ["2026-10-12", 132, 3],
      ["2026-10-13", 0, 0],
      ["2026-10-14", 9, 0],
    ]);
  });
});

describe("bucketLabel", () => {
  it("names a day in UTC with its traces and errors", () => {
    expect(bucketLabel({ start: "2026-10-14T00:00:00.000Z", traces: 132, errors: 3 }, "day")).toBe(
      "Oct 14 · 132 traces · 3 errors",
    );
  });

  it("names an hour by its local start, in the singular for one", () => {
    expect(
      bucketLabel({ start: "2026-10-14T12:00:00.000Z", traces: 1, errors: 1 }, "hour"),
    ).toMatch(/^Oct 14, \d{2}:\d{2} · 1 trace · 1 error$/);
  });
});

describe("bucketWindow", () => {
  const WINDOW = { from: "2026-10-13T09:30:00.000Z", to: "2026-10-15T08:00:00.000Z" };

  it("spans exactly one bucket inside the window", () => {
    expect(bucketWindow("2026-10-14T11:00:00.000Z", "hour", WINDOW)).toEqual({
      from: "2026-10-14T11:00:00.000Z",
      to: "2026-10-14T12:00:00.000Z",
    });
    expect(bucketWindow("2026-10-14T00:00:00.000Z", "day", WINDOW)).toEqual({
      from: "2026-10-14T00:00:00.000Z",
      to: "2026-10-15T00:00:00.000Z",
    });
  });

  it("clips a partial first and last bucket to the window", () => {
    expect(bucketWindow("2026-10-13T00:00:00.000Z", "day", WINDOW)).toEqual({
      from: "2026-10-13T09:30:00.000Z",
      to: "2026-10-14T00:00:00.000Z",
    });
    expect(bucketWindow("2026-10-15T00:00:00.000Z", "day", WINDOW)).toEqual({
      from: "2026-10-15T00:00:00.000Z",
      to: "2026-10-15T08:00:00.000Z",
    });
  });
});

describe("markMinHeight", () => {
  const bar = (traces: number, errors: number) => ({ start: "", traces, errors });

  it("keeps room for both segments and the gap only when a bar has both", () => {
    expect(markMinHeight(bar(1000, 1))).toBe(6);
    expect(markMinHeight(bar(3, 0))).toBe(2);
    expect(markMinHeight(bar(3, 3))).toBe(2);
  });
});
