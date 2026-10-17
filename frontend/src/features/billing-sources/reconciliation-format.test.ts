import { describe, expect, it } from "vitest";

import {
  dayLabel,
  formatSignedUsd,
  formatTokenDifference,
  formatUsd,
  formatVariance,
  sumMoney,
} from "./reconciliation-format";

describe("formatUsd", () => {
  it("rounds half up to cents from the decimal digits, not through a float", () => {
    expect(formatUsd("12.3456")).toBe("$12.35");
    expect(formatUsd("0.125")).toBe("$0.13");
    expect(formatUsd("1234.5")).toBe("$1,234.50");
    expect(formatUsd("0")).toBe("$0.00");
  });

  it("keeps four decimals for an amount under a cent, and never reads a non-zero amount as zero", () => {
    expect(formatUsd("0.0034")).toBe("$0.0034");
    expect(formatUsd("0.00001")).toBe("<$0.0001");
    expect(formatUsd("-0.00001")).toBe(">−$0.0001");
  });

  it("writes a negative amount with a minus sign", () => {
    expect(formatUsd("-5.0000")).toBe("−$5.00");
    expect(formatUsd("-0.31")).toBe("−$0.31");
  });
});

describe("formatSignedUsd", () => {
  it("signs a residual both ways and leaves zero unsigned", () => {
    expect(formatSignedUsd("0.4200")).toBe("+$0.42");
    expect(formatSignedUsd("-1.6900")).toBe("−$1.69");
    expect(formatSignedUsd("0.0000")).toBe("$0.00");
  });
});

describe("sumMoney", () => {
  it("adds decimal strings exactly", () => {
    expect(sumMoney(["0.1", "0.2"])).toBe("0.3");
    expect(sumMoney(["12.3456", "-0.21", "3"])).toBe("15.1356");
    expect(sumMoney([])).toBe("0");
  });
});

describe("formatVariance", () => {
  it("is a signed percentage with one decimal", () => {
    expect(formatVariance("0.0310")).toBe("+3.1 %");
    expect(formatVariance("-0.1550")).toBe("−15.5 %");
    expect(formatVariance("0.0000")).toBe("0.0 %");
  });
});

describe("formatTokenDifference", () => {
  it("signs a token difference and leaves zero bare", () => {
    expect(formatTokenDifference(19_000)).toBe("+19,000");
    expect(formatTokenDifference(-6_000)).toBe("−6,000");
    expect(formatTokenDifference(0)).toBe("0");
  });
});

describe("dayLabel", () => {
  it("names a UTC day without a year", () => {
    expect(dayLabel("2026-10-07")).toBe("Oct 7");
  });
});
