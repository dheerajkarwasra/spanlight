import { describe, expect, it } from "vitest";

import type { CompareStep, StepSpan } from "@/lib/api";

import { stepChanged, stepState } from "./trace-compare";

const side: StepSpan = {
  span_id: "a1",
  kind: "llm",
  status: "ok",
  model: "gpt-4.1-mini",
  duration_ms: 200,
  input_tokens: 10,
  output_tokens: 5,
  cost_usd: "0.001",
};

const same: CompareStep = {
  name: "chat",
  occurrence: 1,
  a: side,
  b: { ...side, span_id: "b1", duration_ms: 260 },
  duration_ms: 60,
  input_tokens: 0,
  output_tokens: 0,
  cost_usd: "0",
  input_changed: false,
  output_changed: false,
};

describe("stepState", () => {
  it("names the missing side before anything else", () => {
    expect(stepState({ ...same, b: null })).toBe("only_a");
    expect(stepState({ ...same, a: null })).toBe("only_b");
  });

  it("puts a failure on one side before the output", () => {
    expect(stepState({ ...same, b: { ...side, status: "error" }, output_changed: true })).toBe(
      "failed_b",
    );
    expect(stepState({ ...same, a: { ...side, status: "error" } })).toBe("failed_a");
  });

  it("keeps an unknown output unknown", () => {
    expect(stepState({ ...same, output_changed: null })).toBe("output_unknown");
    expect(stepState({ ...same, output_changed: true })).toBe("output_changed");
  });
});

describe("stepChanged", () => {
  it("treats a slower step with the same output, input, model and cost as unchanged", () => {
    expect(stepChanged(same)).toBe(false);
  });

  it("flags a changed input, model, token count or cost", () => {
    expect(stepChanged({ ...same, input_changed: true })).toBe(true);
    expect(stepChanged({ ...same, b: { ...side, model: "gpt-4.1" } })).toBe(true);
    expect(stepChanged({ ...same, output_tokens: 3 })).toBe(true);
    expect(stepChanged({ ...same, cost_usd: "0.0004" })).toBe(true);
  });
});
