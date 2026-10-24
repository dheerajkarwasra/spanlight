import { describe, expect, it } from "vitest";

import {
  dayLabel,
  formatClock,
  formatCompactDuration,
  formatDayTime,
  formatSessionWindow,
  isCostOutlier,
  turnRangeLabel,
} from "./session-format";

// Built from local-time parts so the assertions hold in any time zone.
function localIso(day: number, hour: number, minute: number, second = 0, month = 9): string {
  return new Date(2026, month, day, hour, minute, second).toISOString();
}

const NOW = new Date(2026, 9, 8, 12, 0, 0);

describe("dayLabel", () => {
  it("names today and yesterday", () => {
    expect(dayLabel(new Date(2026, 9, 8, 0, 5), NOW)).toBe("Today");
    expect(dayLabel(new Date(2026, 9, 7, 23, 59), NOW)).toBe("Yesterday");
  });

  it("uses the date for older days, with the year only outside the current year", () => {
    expect(dayLabel(new Date(2026, 9, 6, 9, 0), NOW)).toBe("Oct 6");
    expect(dayLabel(new Date(2025, 11, 31, 9, 0), NOW)).toBe("Dec 31, 2025");
  });
});

describe("formatClock", () => {
  it("formats a 24-hour clock, with optional seconds", () => {
    expect(formatClock(localIso(8, 9, 12, 4))).toBe("09:12");
    expect(formatClock(localIso(8, 0, 5, 9), { seconds: true })).toBe("00:05:09");
  });

  it("returns null for an invalid timestamp", () => {
    expect(formatClock("nope")).toBeNull();
  });
});

describe("formatDayTime", () => {
  it("prefixes the day", () => {
    expect(formatDayTime(localIso(8, 9, 12, 4), NOW, { seconds: true })).toBe("Today 09:12:04");
    expect(formatDayTime(localIso(6, 22, 14), NOW)).toBe("Oct 6 22:14");
  });
});

describe("formatSessionWindow", () => {
  it("shows the day once for a session within one day", () => {
    expect(formatSessionWindow(localIso(8, 9, 12), localIso(8, 9, 41), NOW)).toBe(
      "Today 09:12 → 09:41",
    );
  });

  it("repeats the day when the session crosses midnight", () => {
    expect(formatSessionWindow(localIso(7, 23, 50), localIso(8, 0, 10), NOW)).toBe(
      "Yesterday 23:50 → Today 00:10",
    );
  });

  it("returns null when a timestamp is invalid", () => {
    expect(formatSessionWindow("nope", localIso(8, 9, 41), NOW)).toBeNull();
  });
});

describe("formatCompactDuration", () => {
  it.each([
    [400, "<1s"],
    [45_000, "45s"],
    [29 * 60_000 + 33_000, "29m"],
    [2 * 3_600_000, "2h"],
    [2 * 3_600_000 + 5 * 60_000, "2h 5m"],
  ])("formats %i ms as %s", (ms, label) => {
    expect(formatCompactDuration(ms)).toBe(label);
  });

  it("returns null for unknown durations", () => {
    expect(formatCompactDuration(null)).toBeNull();
    expect(formatCompactDuration(Number.NaN)).toBeNull();
  });
});

describe("turnRangeLabel", () => {
  it("describes which turns are on screen", () => {
    expect(turnRangeLabel(6, 12, false)).toBe("Turns 1–6 of 12");
    expect(turnRangeLabel(12, 12, false)).toBe("12 turns");
    expect(turnRangeLabel(1, 1, false)).toBe("1 turn");
    expect(turnRangeLabel(10, 100, true)).toBe("Latest 100 turns");
  });
});

describe("isCostOutlier", () => {
  it("flags only a known cost above a known 99th percentile", () => {
    expect(isCostOutlier("0.50", "0.40")).toBe(true);
    expect(isCostOutlier("0.40", "0.40")).toBe(false);
    expect(isCostOutlier(null, "0.40")).toBe(false);
    expect(isCostOutlier("0.50", null)).toBe(false);
  });
});
