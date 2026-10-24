import { describe, expect, it } from "vitest";

import { shortUsd, tickPositions } from "./budget-ticks";

describe("tickPositions", () => {
  it("marks 50, 80 and 100 % of the amount", () => {
    expect(tickPositions(500)).toEqual([
      { pct: 50, usd: "$250" },
      { pct: 80, usd: "$400" },
      { pct: 100, usd: "$500" },
    ]);
  });

  it("keeps cents and small amounts", () => {
    expect(tickPositions(12.5).map((tick) => tick.usd)).toEqual(["$6.25", "$10", "$12.50"]);
    expect(tickPositions(0.01).map((tick) => tick.usd)).toEqual(["$0.005", "$0.008", "$0.01"]);
  });
});

describe("shortUsd", () => {
  it("drops .00 from whole dollars only", () => {
    expect(shortUsd(182)).toBe("$182");
    expect(shortUsd(182.4)).toBe("$182.40");
    expect(shortUsd(1500)).toBe("$1,500");
  });
});
