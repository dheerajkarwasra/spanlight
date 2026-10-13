import { describe, expect, it } from "vitest";

import { fittedMetricFontSize, metricEmWidth } from "./metric-fit";

/**
 * Advance widths measured in Figma for Plus Jakarta Sans ExtraBold at the Metric/L tracking
 * (-3%), in em. The estimate may overshoot them but never fall short: falling short is what
 * clipped "100.0%" to "100. …" at 375 px.
 */
const MEASURED_EM: Record<string, number> = {
  "100.0%": 3.86,
  "1,234,567": 4.61,
  "48,210": 3.25,
  "0.82%": 3.31,
  "122 ms": 3.08,
  "2m 03s": 3.33,
};

describe("metricEmWidth", () => {
  it.each(Object.entries(MEASURED_EM))("never underestimates %s", (text, measured) => {
    expect(metricEmWidth(text)).toBeGreaterThanOrEqual(measured);
  });

  it("stays within 15% of the measured width, so short values are not shrunk for nothing", () => {
    for (const [text, measured] of Object.entries(MEASURED_EM)) {
      expect(metricEmWidth(text)).toBeLessThanOrEqual(measured * 1.15);
    }
  });

  it("is zero for an empty string", () => {
    expect(metricEmWidth("")).toBe(0);
  });
});

describe("fittedMetricFontSize", () => {
  it("caps the size at the metric token and divides the container width by the widest value", () => {
    const widest = metricEmWidth("100.0%");
    expect(fittedMetricFontSize(["1", "100.0%", "0", "122 ms"])).toBe(
      `min(var(--text-metric), calc(100cqi / ${widest.toFixed(2)}))`,
    );
  });

  it("ignores unknown values, which render as a dash", () => {
    expect(fittedMetricFontSize([null, "100.0%", null])).toBe(fittedMetricFontSize(["100.0%"]));
  });

  it("leaves the size alone when there is no value to fit", () => {
    expect(fittedMetricFontSize([null, null])).toBeUndefined();
    expect(fittedMetricFontSize([])).toBeUndefined();
  });
});
