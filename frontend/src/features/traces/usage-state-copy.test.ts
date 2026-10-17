import { describe, expect, it } from "vitest";

import { NO_PRICE_REASON } from "@/components/cost-value";
import type { UsageState } from "@/lib/api";

import { usageReason, type UsageField } from "./usage-state-copy";

const FIELDS: readonly UsageField[] = ["input", "output", "cost"];

function reasons(state: UsageState): (string | null)[] {
  return FIELDS.map((field) => usageReason(state, field));
}

describe("usageReason", () => {
  it("explains nothing for a priced span", () => {
    expect(reasons("priced")).toEqual([null, null, null]);
  });

  it("explains nothing on a rollup: the values are shown as sent and the badge carries the copy", () => {
    expect(reasons("rollup")).toEqual([null, null, null]);
  });

  it("explains nothing for a span that is not a model call", () => {
    expect(reasons("not_applicable")).toEqual([null, null, null]);
  });

  it("says the exporter sent no token counts on every usage cell", () => {
    expect(reasons("usage_not_reported")).toEqual([
      "The exporter sent no token counts",
      "The exporter sent no token counts",
      "The exporter sent no token counts",
    ]);
  });

  it("names the count that did arrive on the missing cell, and why the cost is unknown", () => {
    expect(reasons("partial_usage")).toEqual([
      "The exporter sent only output tokens",
      "The exporter sent only input tokens",
      "Usage is incomplete, so no cost was computed",
    ]);
  });

  it("blames the missing model name only for the cost", () => {
    expect(reasons("model_unknown")).toEqual([null, null, "No model name on this span"]);
  });

  it("blames the price only for the cost", () => {
    expect(reasons("no_price")).toEqual([null, null, NO_PRICE_REASON]);
  });
});
