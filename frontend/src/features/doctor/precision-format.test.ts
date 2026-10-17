import { describe, expect, it } from "vitest";

import { ApiError, type PrecisionKind } from "@/lib/api";

import {
  formatPrecisionRow,
  hasAnswers,
  intervalFootnote,
  PRECISION_TARGET,
  precisionErrorText,
  sortPrecisionRows,
  targetLine,
} from "./precision-format";

function kind(overrides: Partial<PrecisionKind> = {}): PrecisionKind {
  return {
    kind: "retry_storm",
    label: "Retry storm",
    right: 23,
    wrong: 2,
    n: 25,
    precision: "0.9200",
    wilson_low: "0.7503",
    wilson_high: "0.9778",
    meets_target: true,
    ...overrides,
  };
}

describe("formatPrecisionRow", () => {
  it("shows precision and the Wilson interval to two places", () => {
    const row = formatPrecisionRow(kind());
    expect(row.precision).toBe("0.92");
    expect(row.interval).toBe("0.75–0.98");
    expect(row.status).toBe("meets");
    expect(row.note).toBeNull();
  });

  it("marks a kind with enough answers below the target", () => {
    const row = formatPrecisionRow(
      kind({ right: 11, wrong: 9, n: 20, precision: "0.5500", meets_target: false }),
    );
    expect(row.status).toBe("below");
    expect(row.note).toBeNull();
  });

  it("never marks a kind under 20 answers", () => {
    const row = formatPrecisionRow(
      kind({ right: 14, wrong: 1, n: 15, precision: "0.9333", meets_target: false }),
    );
    expect(row.status).toBe("insufficient");
    expect(row.note).toBe("not enough feedback yet");
    expect(row.precision).toBe("0.93");
  });

  it("keeps the ratios unknown when nobody answered", () => {
    const row = formatPrecisionRow(
      kind({
        right: 0,
        wrong: 0,
        n: 0,
        precision: null,
        wilson_low: null,
        wilson_high: null,
        meets_target: false,
      }),
    );
    expect(row.precision).toBeNull();
    expect(row.interval).toBeNull();
    expect(row.bar).toBeNull();
    expect(row.status).toBe("none");
    expect(row.note).toBe("no answers yet");
  });

  it("places the interval and the point on a 0–100 scale", () => {
    expect(formatPrecisionRow(kind()).bar).toEqual({ low: 75.03, high: 97.78, point: 92 });
  });

  it("treats an unreadable decimal as unknown", () => {
    const row = formatPrecisionRow(kind({ precision: "n/a" }));
    expect(row.precision).toBeNull();
    expect(row.bar).toBeNull();
  });
});

describe("the target line", () => {
  it("is precision 0.8, lower bound 0.6, 20 answers", () => {
    expect(PRECISION_TARGET).toEqual({ precision: 0.8, lowerBound: 0.6, minAnswers: 20 });
    expect(targetLine()).toBe("Target: precision ≥ 0.8, lower bound ≥ 0.6, at least 20 answers.");
  });
});

describe("sortPrecisionRows", () => {
  it("puts the kinds with the most answers first, then by name", () => {
    const rows = [
      kind({ kind: "a", label: "Unpriced spend", n: 3 }),
      kind({ kind: "b", label: "Retry storm", n: 25 }),
      kind({ kind: "c", label: "Provider incident", n: 6 }),
      kind({ kind: "d", label: "Prompt-cache opportunity", n: 6 }),
      kind({ kind: "e", label: "Truncated outputs", n: 0 }),
    ];
    expect(sortPrecisionRows(rows).map((row) => row.kind)).toEqual(["b", "d", "c", "a", "e"]);
  });
});

describe("hasAnswers", () => {
  it("is false when every kind has n = 0 or there are no kinds", () => {
    expect(hasAnswers([])).toBe(false);
    expect(hasAnswers([kind({ n: 0 })])).toBe(false);
    expect(hasAnswers([kind({ n: 0 }), kind({ n: 1 })])).toBe(true);
  });
});

describe("intervalFootnote", () => {
  it("lists the Wilson range of every kind with enough answers", () => {
    const rows = [
      kind(),
      kind({ label: "Error spike", wilson_low: "0.6500", wilson_high: "0.9500", n: 21 }),
      kind({ label: "Rate-limit pressure", n: 15 }),
    ];
    expect(intervalFootnote(rows)).toBe(
      "Wilson ranges: Retry storm 0.75–0.98 · Error spike 0.65–0.95.",
    );
  });

  it("is null when no kind has enough answers", () => {
    expect(intervalFootnote([kind({ n: 4 })])).toBeNull();
  });
});

describe("precisionErrorText", () => {
  it("names the server's status and says the other tabs still work", () => {
    expect(precisionErrorText(new ApiError(503, { status: 503 }))).toBe(
      "The server answered 503. The insight tabs still work.",
    );
  });
});
