import { describe, expect, it } from "vitest";

import {
  compactRelative,
  formatWindow,
  insightKindLabel,
  lastCheckedText,
  splitInlineCode,
} from "./insight-format";

const NOW = new Date("2026-10-13T12:00:00Z");

describe("insightKindLabel", () => {
  it("names known detectors and spells out unknown kinds", () => {
    expect(insightKindLabel("retry_storm")).toBe("Retry storm");
    expect(insightKindLabel("new_detector_kind")).toBe("New detector kind");
    expect(insightKindLabel("___")).toBe("___");
  });
});

describe("compactRelative", () => {
  it("never reads a clock-skewed future time as negative", () => {
    expect(compactRelative("2026-10-13T12:05:00Z", NOW)).toBe("just now");
  });

  it("is null for a missing or unreadable time", () => {
    expect(compactRelative(null, NOW)).toBeNull();
    expect(compactRelative("yesterday", NOW)).toBeNull();
  });

  it("picks the largest whole unit", () => {
    expect(compactRelative("2026-10-13T11:59:28Z", NOW)).toBe("32 s ago");
    expect(compactRelative("2026-10-13T09:00:00Z", NOW)).toBe("3 h ago");
    expect(compactRelative("2026-10-11T11:00:00Z", NOW)).toBe("2 d ago");
  });
});

describe("formatWindow", () => {
  it("states the window length in minutes, or whole hours from 2 h", () => {
    expect(formatWindow("2026-10-13T10:00:00Z", "2026-10-13T10:15:00Z")).toMatch(
      /· 15 min window$/,
    );
    expect(formatWindow("2026-10-13T10:00:00Z", "2026-10-13T11:00:00Z")).toMatch(
      /· 60 min window$/,
    );
    expect(formatWindow("2026-10-13T10:00:00Z", "2026-10-13T13:00:00Z")).toMatch(/· 3 h window$/);
  });

  it("is null when either end is unreadable", () => {
    expect(formatWindow("bad", "2026-10-13T10:15:00Z")).toBeNull();
  });
});

describe("lastCheckedText", () => {
  const base = { pending: false, failed: false, runFailed: false, ranAt: null };

  it("does not claim a check happened when it is unknown", () => {
    expect(lastCheckedText({ ...base, pending: true }, NOW)).toBe("Last checked —");
    expect(lastCheckedText({ ...base, failed: true }, NOW)).toBe("Last check unknown");
    expect(lastCheckedText(base, NOW)).toBe("Not checked yet");
  });

  it("says when the last run failed", () => {
    const ranAt = "2026-10-13T11:46:00Z";
    expect(lastCheckedText({ ...base, ranAt }, NOW)).toBe("Last checked 14 min ago");
    expect(lastCheckedText({ ...base, ranAt, runFailed: true }, NOW)).toBe(
      "Last check failed 14 min ago",
    );
  });
});

describe("splitInlineCode", () => {
  it("marks backticked pieces as code and keeps markup as text", () => {
    expect(splitInlineCode("Set `timeout=30` on <client>")).toEqual([
      { text: "Set ", code: false },
      { text: "timeout=30", code: true },
      { text: " on <client>", code: false },
    ]);
  });
});
