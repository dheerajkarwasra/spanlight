import { describe, expect, it } from "vitest";

import { buildSparkline, SPARKLINE_BOX } from "./sparkline-path";

describe("buildSparkline", () => {
  it("draws nothing without data", () => {
    expect(buildSparkline([])).toEqual({ paths: [], end: null });
    expect(buildSparkline([null, null])).toEqual({ paths: [], end: null });
  });

  it("spans the inset box, highest value at the top", () => {
    const { paths, end } = buildSparkline([0, 10]);
    expect(paths).toEqual(["M3 28 C48 28 48 4 93 4"]);
    expect(end).toEqual({ x: SPARKLINE_BOX.width - SPARKLINE_BOX.insetX, y: SPARKLINE_BOX.insetY });
  });

  it("draws a flat series through the middle", () => {
    const { paths } = buildSparkline([5, 5, 5]);
    expect(paths[0]).toMatch(/^M3 16 /);
  });

  it("breaks the line at unknown buckets and dots the latest known value", () => {
    const { paths, end } = buildSparkline([1, 2, null, 3, 4, null]);
    expect(paths).toHaveLength(2);
    expect(end?.x).toBeCloseTo(3 + (4 * 90) / 5);
  });

  it("dots a lone value without drawing a line", () => {
    const { paths, end } = buildSparkline([null, 7]);
    expect(paths).toEqual([]);
    expect(end).not.toBeNull();
  });
});
