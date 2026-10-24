import { describe, expect, it } from "vitest";

import type { PrecisionKind } from "@/lib/api";

import { trackRecordText } from "./track-record";

function row(overrides: Partial<PrecisionKind> = {}): PrecisionKind {
  return {
    kind: "error_spike",
    label: "Error spike",
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

describe("trackRecordText", () => {
  it("states the record with precision and its interval from 20 votes on", () => {
    expect(trackRecordText(row())).toBe(
      "This check was right 23 of 25 times (precision 0.92, 0.75–0.98)",
    );
  });

  it("says how many votes are still missing under 20", () => {
    expect(
      trackRecordText(row({ right: 2, wrong: 1, n: 3, precision: "0.6667", meets_target: false })),
    ).toBe("Not enough votes yet (3 of 20)");
  });

  it("says there are no votes when the check has none", () => {
    expect(trackRecordText(undefined)).toBe("No votes on this check yet");
    expect(
      trackRecordText(
        row({ right: 0, wrong: 0, n: 0, precision: null, wilson_low: null, wilson_high: null }),
      ),
    ).toBe("No votes on this check yet");
  });
});
