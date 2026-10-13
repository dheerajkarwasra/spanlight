import { describe, expect, it } from "vitest";

import {
  barFill,
  formatUsd,
  isExceeded,
  percentLabel,
  resetsInLabel,
  spendRatio,
} from "./budget-period";

describe("formatUsd", () => {
  it("is null for unknown spend, never $0.00", () => {
    expect(formatUsd(null)).toBeNull();
    expect(formatUsd("")).toBeNull();
    expect(formatUsd("not-a-number")).toBeNull();
  });

  it("keeps up to 4 decimals and marks dust", () => {
    expect(formatUsd("0")).toBe("$0.00");
    expect(formatUsd("0.0125")).toBe("$0.0125");
    expect(formatUsd("0.00004")).toBe("< $0.0001");
    expect(formatUsd("1234.5")).toBe("$1,234.50");
  });
});

describe("spend against the amount", () => {
  it("has no ratio while the spend is unknown", () => {
    expect(spendRatio(null, "50")).toBeNull();
    expect(spendRatio("10", "0")).toBeNull();
  });

  it("never rounds a not-yet-exceeded budget up to 100 %", () => {
    expect(percentLabel(0.996)).toBe("99 %");
    expect(percentLabel(0.29)).toBe("29 %");
    expect(percentLabel(1.05)).toBe("105 %");
  });

  it("fires strictly above the amount, as the server does", () => {
    expect(isExceeded(spendRatio("50", "50"))).toBe(false);
    expect(isExceeded(spendRatio("50.0001", "50"))).toBe(true);
    expect(isExceeded(null)).toBe(false);
  });

  it("caps the bar between empty and full", () => {
    expect(barFill(2.5)).toBe(1);
    expect(barFill(-0.1)).toBe(0);
  });
});

describe("resetsInLabel", () => {
  const now = new Date("2026-10-31T23:30:00Z");

  it("counts down to the UTC reset across a month boundary", () => {
    expect(resetsInLabel("2026-11-01T00:00:00Z", now)).toBe("Resets in 30 min");
    expect(resetsInLabel("2026-11-01T00:00:00Z", new Date("2026-10-30T20:00:00Z"))).toBe(
      "Resets in 1 d 4 h",
    );
    expect(resetsInLabel("2026-11-01T00:00:00Z", new Date("2026-10-31T08:40:00Z"))).toBe(
      "Resets in 15 h 20 min",
    );
  });

  it("says 'now' once the reset has passed and handles the last minute", () => {
    expect(resetsInLabel("2026-10-31T23:00:00Z", now)).toBe("Resets now");
    expect(resetsInLabel("2026-10-31T23:30:30Z", now)).toBe("Resets in under a minute");
  });

  it("is null for an unreadable time", () => {
    expect(resetsInLabel("soon", now)).toBeNull();
  });
});
