import { describe, expect, it } from "vitest";

import {
  clampWindow,
  defaultWindow,
  windowAccessibleName,
  windowDays,
  windowFromDays,
  windowFromSearch,
  windowLabel,
} from "./window-picker";

const NOW = new Date("2026-10-16T12:00:00Z");

describe("clampWindow", () => {
  it("widens a partial day to whole UTC days", () => {
    expect(clampWindow("2026-10-01T13:45:00Z", "2026-10-05T08:00:00Z", NOW)).toEqual({
      from: "2026-10-01T00:00:00Z",
      to: "2026-10-06T00:00:00Z",
      error: null,
    });
  });

  it("keeps a window that is already whole days", () => {
    expect(clampWindow("2026-10-02T00:00:00Z", "2026-10-16T00:00:00Z", NOW)).toEqual({
      from: "2026-10-02T00:00:00Z",
      to: "2026-10-16T00:00:00Z",
      error: null,
    });
  });

  it("accepts 31 days and refuses 32", () => {
    expect(clampWindow("2026-09-01T00:00:00Z", "2026-10-02T00:00:00Z", NOW).error).toBeNull();
    const tooLong = clampWindow("2026-09-01T00:00:00Z", "2026-10-03T00:00:00Z", NOW);
    expect(tooLong.error).toBe("The window may span at most 31 whole UTC days.");
  });

  it("counts the widened days, so a partial day can push a window past 31", () => {
    const widened = clampWindow("2026-09-01T12:00:00Z", "2026-10-02T00:00:01Z", NOW);
    expect(widened.error).toBe("The window may span at most 31 whole UTC days.");
  });

  it("accepts today and refuses a window ending after it", () => {
    expect(clampWindow("2026-10-10T00:00:00Z", "2026-10-17T00:00:00Z", NOW).error).toBeNull();
    const future = clampWindow("2026-10-10T00:00:00Z", "2026-10-18T00:00:00Z", NOW);
    expect(future.error).toBe("The window may not end after today (UTC).");
  });

  it("refuses an end before the start and an unreadable date", () => {
    expect(clampWindow("2026-10-10T00:00:00Z", "2026-10-05T00:00:00Z", NOW).error).toBe(
      "The first day must come before the last day.",
    );
    expect(clampWindow("yesterday", "2026-10-05T00:00:00Z", NOW).error).toBe(
      "Pick a first and a last day.",
    );
  });
});

describe("windowFromDays", () => {
  it("turns an inclusive first and last day into the API's window", () => {
    expect(windowFromDays("2026-10-01", "2026-10-14", NOW)).toEqual({
      from: "2026-10-01T00:00:00Z",
      to: "2026-10-15T00:00:00Z",
      error: null,
    });
  });

  it("refuses a last day after today", () => {
    expect(windowFromDays("2026-10-10", "2026-10-17", NOW).error).toBe(
      "The window may not end after today (UTC).",
    );
  });
});

describe("defaultWindow and windowFromSearch", () => {
  it("defaults to the last 14 whole days before today", () => {
    expect(defaultWindow(NOW)).toEqual({
      from: "2026-10-02T00:00:00Z",
      to: "2026-10-16T00:00:00Z",
    });
  });

  it("reads the URL's first and last day, falling back to the default when invalid", () => {
    expect(windowFromSearch({ first: "2026-10-01", last: "2026-10-07" }, NOW)).toEqual({
      from: "2026-10-01T00:00:00Z",
      to: "2026-10-08T00:00:00Z",
    });
    expect(windowFromSearch({ first: "2026-08-01", last: "2026-10-07" }, NOW)).toEqual(
      defaultWindow(NOW),
    );
    expect(windowFromSearch({}, NOW)).toEqual(defaultWindow(NOW));
  });
});

describe("window labels", () => {
  const window = { from: "2026-10-01T00:00:00Z", to: "2026-10-15T00:00:00Z" };

  it("names the first and last day and the count", () => {
    expect(windowLabel(window)).toBe("Oct 1 – Oct 14 · 14 days");
    expect(windowLabel({ from: "2026-10-14T00:00:00Z", to: "2026-10-15T00:00:00Z" })).toBe(
      "Oct 14 · 1 day",
    );
    expect(windowAccessibleName(window)).toBe("Window, Oct 1 to Oct 14, 14 days, UTC");
  });

  it("gives the inclusive days for the date fields", () => {
    expect(windowDays(window)).toEqual({ first: "2026-10-01", last: "2026-10-14", count: 14 });
  });
});
