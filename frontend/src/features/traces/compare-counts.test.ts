import { describe, expect, it } from "vitest";

import type { CompareStep, StepSpan } from "@/lib/api";

import { onlyInCounts } from "./compare-counts";

const side: StepSpan = {
  span_id: "s1",
  kind: "llm",
  status: "ok",
  model: "gpt-4.1-mini",
  duration_ms: 200,
  input_tokens: 10,
  output_tokens: 5,
  cost_usd: "0.001",
};

function step(name: string, a: StepSpan | null, b: StepSpan | null): CompareStep {
  return {
    name,
    occurrence: 1,
    a,
    b,
    duration_ms: null,
    input_tokens: null,
    output_tokens: null,
    cost_usd: null,
    input_changed: null,
    output_changed: null,
  };
}

describe("onlyInCounts", () => {
  it("counts the steps missing from the other trace, per side", () => {
    const steps = [
      step("both", side, side),
      step("only a 1", side, null),
      step("only a 2", side, null),
      step("only b", null, side),
    ];
    expect(onlyInCounts(steps)).toEqual({ a: 2, b: 1 });
  });

  it("is zero on both sides when every step is in both traces", () => {
    expect(onlyInCounts([step("both", side, side)])).toEqual({ a: 0, b: 0 });
    expect(onlyInCounts([])).toEqual({ a: 0, b: 0 });
  });
});
