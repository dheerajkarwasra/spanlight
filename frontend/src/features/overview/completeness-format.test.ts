import { describe, expect, it } from "vitest";

import type { Completeness } from "@/lib/api";

import {
  completenessReason,
  completenessText,
  formatCompleteness,
  lossTiles,
  SDK_UNREPORTED_REASON,
  storedLine,
} from "./completeness-format";

const NBSP = " ";

function result(overrides: Partial<Completeness>): Completeness {
  return {
    stored_spans: 128_406,
    losses: [],
    completeness: "1",
    lower_bound: false,
    ...overrides,
  };
}

describe("formatCompleteness", () => {
  it("shows four places of the ratio as a percentage with two decimals", () => {
    expect(formatCompleteness("0.9987")).toBe(`99.87${NBSP}%`);
  });

  it("shows zero as 0 %", () => {
    expect(formatCompleteness("0")).toBe(`0${NBSP}%`);
  });

  it("shows an unknown ratio as a dash", () => {
    expect(formatCompleteness(null)).toBe("—");
  });

  it("never rounds up: 0.9999 is 99.99 %, not 100 %", () => {
    expect(formatCompleteness("0.9999")).toBe(`99.99${NBSP}%`);
  });

  it("cuts extra places instead of rounding them", () => {
    expect(formatCompleteness("0.99999")).toBe(`99.99${NBSP}%`);
  });

  it("drops trailing zeros", () => {
    expect(formatCompleteness("1")).toBe(`100${NBSP}%`);
    expect(formatCompleteness("0.5")).toBe(`50${NBSP}%`);
    expect(formatCompleteness("0.9990")).toBe(`99.9${NBSP}%`);
    expect(formatCompleteness("0.0005")).toBe(`0.05${NBSP}%`);
  });

  it("shows a value it cannot read as a dash", () => {
    expect(formatCompleteness("not a number")).toBe("—");
    expect(formatCompleteness("1.5")).toBe("—");
  });
});

describe("completenessText", () => {
  it("is null when the ratio is unknown", () => {
    expect(completenessText(result({ completeness: null, stored_spans: 0 }))).toBeNull();
  });

  it("is 100 % only when nothing was lost", () => {
    expect(completenessText(result({ completeness: "1" }))).toBe(`100${NBSP}%`);
  });

  it("never shows 100 % while losses exist", () => {
    const lossy = result({
      completeness: "1",
      losses: [{ source: "ingest", reason: "validation", count: 1 }],
    });
    expect(completenessText(lossy)).toBe(`99.99${NBSP}%`);
  });
});

describe("completenessReason", () => {
  it("defines the ratio", () => {
    expect(completenessReason(result({}))).toContain("stored ÷ (stored + every span lost");
  });

  it("names the SDK failed and ingest rejected overlap for a lower bound", () => {
    const reason = completenessReason(result({ lower_bound: true }));
    expect(reason).toContain("lower bound");
    expect(reason).toContain("the SDK reports as failed");
    expect(reason).toContain("rejected");
  });

  it("keeps the definition first and says there is no ratio when nothing was stored or lost", () => {
    const reason = completenessReason(result({ completeness: null, stored_spans: 0 }));
    expect(reason.startsWith("Share of spans that reached storage")).toBe(true);
    expect(reason).toContain("Nothing was stored or lost in this window, so there is no ratio.");
  });
});

describe("lossTiles", () => {
  it("keeps the three sources in order; the server's zeros are none, the SDK's are unknown", () => {
    expect(lossTiles([])).toEqual([
      { source: "gateway", label: "Gateway", count: 0, detail: "none", unknownReason: null },
      { source: "ingest", label: "Ingest rejected", count: 0, detail: "none", unknownReason: null },
      {
        source: "sdk",
        label: "SDK dropped or failed",
        count: null,
        detail: "none reported",
        unknownReason: SDK_UNREPORTED_REASON,
      },
    ]);
    expect(SDK_UNREPORTED_REASON).toBe(
      "Reported by the Python SDK from 0.2.0; other senders don't report their losses",
    );
  });

  it("sums each source and lists its reasons, largest first", () => {
    const tiles = lossTiles([
      { source: "ingest", reason: "too_old", count: 3 },
      { source: "gateway", reason: "overflow", count: 3 },
      { source: "ingest", reason: "validation", count: 9 },
      { source: "sdk", reason: "dropped", count: 2 },
      { source: "sdk", reason: "failed", count: 5 },
    ]);
    expect(tiles.map((tile) => [tile.count, tile.detail])).toEqual([
      [3, "overflow 3"],
      [12, "validation 9 · too_old 3"],
      [7, "failed 5 · dropped 2"],
    ]);
  });

  it("adds a tile for a source it does not know, under its own name", () => {
    const tiles = lossTiles([{ source: "collector", reason: "other", count: 4 }]);
    expect(tiles[3]).toEqual({
      source: "collector",
      label: "collector",
      count: 4,
      detail: "other 4",
      unknownReason: null,
    });
  });
});

describe("storedLine", () => {
  it("counts stored and lost spans", () => {
    const lossy = result({ losses: [{ source: "gateway", reason: "overflow", count: 22 }] });
    expect(storedLine(lossy)).toBe("128,406 spans stored · 22 lost");
  });

  it("says no losses were reported, not that none happened, when nothing was reported", () => {
    expect(storedLine(result({ stored_spans: 1 }))).toBe("1 span stored · no losses reported");
  });

  it("says nothing happened when the ratio is unknown", () => {
    expect(storedLine(result({ completeness: null, stored_spans: 0 }))).toBe(
      "No spans stored or lost in this window",
    );
  });
});
