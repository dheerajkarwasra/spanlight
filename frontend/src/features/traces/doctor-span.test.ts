import { describe, expect, it } from "vitest";

import { doctorSpanId } from "./doctor-span";
import { testSpan } from "./span-test-data";

// Mirrors backend/app/insights/explain_queries.py `trace_excerpts`: failed first, LLM first,
// then start time, then span id.
describe("doctorSpanId", () => {
  const root = testSpan("root", null, 0, 100, { kind: "chain" });
  const llm = testSpan("llm", "root", 10, 10, { kind: "llm" });
  const llmLater = testSpan("llm-b", "root", 50, 10, { kind: "llm" });
  const failedTool = testSpan("tool", "root", 5, 10, { kind: "tool", status: "error" });
  const failedLlm = testSpan("llm-err", "root", 60, 10, { kind: "llm", status: "error" });

  it("prefers the first failed LLM span", () => {
    expect(doctorSpanId([root, llm, failedTool, failedLlm])).toBe("llm-err");
  });

  it("takes the first failed span when no LLM span failed", () => {
    expect(doctorSpanId([root, llm, failedTool])).toBe("tool");
  });

  it("takes the first LLM span when nothing failed", () => {
    expect(doctorSpanId([llmLater, root, llm])).toBe("llm");
  });

  it("takes the first span, then the lower id on a tie, else null", () => {
    const twin = testSpan("a", null, 0, 100, { kind: "chain" });
    expect(doctorSpanId([root, twin])).toBe("a");
    expect(doctorSpanId([])).toBeNull();
  });
});
