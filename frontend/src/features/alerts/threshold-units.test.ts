import { describe, expect, it } from "vitest";

import { fromApiThreshold, shiftDecimal, thresholdError, toApiThreshold } from "./threshold-units";

// The API stores thresholds as numeric(21,6): ratios for error rate, milliseconds for latency,
// dollars for cost. The editor shows percent, seconds and dollars. A wrong shift alerts at a
// value 100x or 1000x off what the user typed.

describe("toApiThreshold", () => {
  it("turns percent into an exact ratio string", () => {
    expect(toApiThreshold("error_rate", "5")).toBe("0.05");
    expect(toApiThreshold("error_rate", "12.5")).toBe("0.125");
    expect(toApiThreshold("error_rate", "0.0001")).toBe("0.000001");
  });

  it("turns seconds into milliseconds without float error", () => {
    expect(toApiThreshold("p95_ms", "1.5")).toBe("1500");
    expect(toApiThreshold("ttft_p95_ms", "0.1")).toBe("100");
    expect(toApiThreshold("p95_ms", "0.000000001")).toBe("0.000001");
  });

  it("keeps dollars and counts as typed, normalising zeros", () => {
    expect(toApiThreshold("cost_usd", "0.10")).toBe("0.1");
    expect(toApiThreshold("tokens", "007")).toBe("7");
    expect(toApiThreshold("llm_calls", " 0 ")).toBe("0");
  });
});

describe("fromApiThreshold", () => {
  it("reads the server's zero-padded decimals back into the editor's unit", () => {
    expect(fromApiThreshold("error_rate", "0.050000")).toBe("5");
    expect(fromApiThreshold("p95_ms", "1500.000000")).toBe("1.5");
    expect(fromApiThreshold("cost_usd", "0.000000")).toBe("0");
  });

  it("is empty for a missing or unreadable threshold, never 0", () => {
    expect(fromApiThreshold("cost_usd", null)).toBe("");
    expect(fromApiThreshold("cost_usd", "1E-7")).toBe("");
  });

  it("round-trips every value the editor accepts", () => {
    const typed: [Parameters<typeof toApiThreshold>[0], string][] = [
      ["error_rate", "0.0001"],
      ["error_rate", "99.99"],
      ["p95_ms", "123.456789012"],
      ["cost_usd", "999999999999999.999999"],
    ];
    for (const [metric, value] of typed) {
      expect(thresholdError(metric, value)).toBeNull();
      expect(fromApiThreshold(metric, toApiThreshold(metric, value))).toBe(value);
    }
  });
});

describe("thresholdError", () => {
  it("refuses blank, negative, exponent and non-numeric input instead of sending 0", () => {
    for (const typed of ["", "  ", "-1", "1e3", "abc", "5%", "1,5"]) {
      expect(thresholdError("cost_usd", typed)).toBe("Enter a number, 0 or higher.");
    }
  });

  it("allows only the decimals the API's 6 places can hold after the shift", () => {
    expect(thresholdError("error_rate", "0.0001")).toBeNull();
    expect(thresholdError("error_rate", "0.00001")).toBe("Use at most 4 decimal places.");
    expect(thresholdError("p95_ms", "0.000000001")).toBeNull();
    expect(thresholdError("p95_ms", "0.0000000001")).toBe("Use at most 9 decimal places.");
    expect(thresholdError("cost_usd", "0.0000001")).toBe("Use at most 6 decimal places.");
  });

  it("caps the whole part at the 15 digits numeric(21,6) holds, after the shift", () => {
    expect(thresholdError("cost_usd", "9".repeat(15))).toBeNull();
    expect(thresholdError("cost_usd", "9".repeat(16))).toBe("Enter a smaller number.");
    // 13 digits of seconds become 16 digits of milliseconds.
    expect(thresholdError("p95_ms", "9".repeat(13))).toBe("Enter a smaller number.");
  });
});

describe("shiftDecimal", () => {
  it("pads with zeros on either side", () => {
    expect(shiftDecimal("5", -3)).toBe("0.005");
    expect(shiftDecimal("0.5", 4)).toBe("5000");
    expect(shiftDecimal("0", 3)).toBe("0");
  });
});
