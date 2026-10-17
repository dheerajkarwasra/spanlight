import { describe, expect, it } from "vitest";

import { computedAtLabel } from "./computed-at";

const NOW = new Date("2026-10-16T12:00:00Z");

function minutesBefore(minutes: number): string {
  return new Date(NOW.getTime() - minutes * 60_000).toISOString();
}

describe("computedAtLabel", () => {
  it("reads minutes for a recompute under an hour ago", () => {
    expect(computedAtLabel(minutesBefore(4), NOW)).toBe("Updated 4 min ago");
  });

  it("rounds a partial minute down", () => {
    expect(computedAtLabel(minutesBefore(4.9), NOW)).toBe("Updated 4 min ago");
  });

  it("reads hours from one hour on", () => {
    expect(computedAtLabel(minutesBefore(120), NOW)).toBe("Updated 2 h ago");
    expect(computedAtLabel(minutesBefore(60), NOW)).toBe("Updated 1 h ago");
  });

  it("reads days from one day on", () => {
    expect(computedAtLabel(minutesBefore(3 * 24 * 60), NOW)).toBe("Updated 3 d ago");
  });

  it("says just now under a minute, and for a time slightly in the future (clock skew)", () => {
    expect(computedAtLabel(minutesBefore(0.5), NOW)).toBe("Updated just now");
    expect(computedAtLabel(minutesBefore(-2), NOW)).toBe("Updated just now");
  });

  it("returns null when the figures were never computed", () => {
    expect(computedAtLabel(null, NOW)).toBeNull();
  });

  it("returns null for a value that is not a timestamp", () => {
    expect(computedAtLabel("not-a-date", NOW)).toBeNull();
  });
});
