import { describe, expect, it } from "vitest";

import { loadedSummary, tracesNoun, windowCountLine } from "./list-summary";

describe("loadedSummary", () => {
  it("shows loaded of total for an unfiltered list", () => {
    expect(loadedSummary({ loaded: 50, total: 12_904, filtered: false, hasNextPage: true })).toBe(
      "Showing 50 of 12,904 traces",
    );
    expect(loadedSummary({ loaded: 1, total: 1, filtered: false, hasNextPage: false })).toBe(
      "Showing 1 of 1 trace",
    );
  });

  it("drops the window total when facet filters narrow the list", () => {
    expect(loadedSummary({ loaded: 50, total: 900, filtered: true, hasNextPage: true })).toBe(
      "Showing 50 matching traces",
    );
    expect(loadedSummary({ loaded: 3, total: 900, filtered: true, hasNextPage: false })).toBe(
      "All 3 matching traces loaded",
    );
    expect(loadedSummary({ loaded: 1, total: 900, filtered: true, hasNextPage: false })).toBe(
      "1 matching trace",
    );
  });

  it("never claims more rows than the total while the total is unknown or stale", () => {
    expect(loadedSummary({ loaded: 50, total: null, filtered: false, hasNextPage: true })).toBe(
      "Showing 50 traces",
    );
    expect(loadedSummary({ loaded: 52, total: 50, filtered: false, hasNextPage: false })).toBe(
      "All 52 traces loaded",
    );
  });
});

describe("tracesNoun", () => {
  it("is singular only for one", () => {
    expect(tracesNoun(0)).toBe("traces");
    expect(tracesNoun(1)).toBe("trace");
    expect(tracesNoun(2)).toBe("traces");
  });
});

describe("windowCountLine", () => {
  it("names the window total, and says it is before filters when facets narrow the list", () => {
    expect(windowCountLine(12_904, "in the last 24 hours", false)).toBe(
      "12,904 traces in the last 24 hours",
    );
    expect(windowCountLine(1, "in the last hour", false)).toBe("1 trace in the last hour");
    expect(windowCountLine(3, "in the last hour", true)).toBe(
      "3 traces in the last hour before filters",
    );
  });
});
