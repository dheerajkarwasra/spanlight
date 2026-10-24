import { describe, expect, it } from "vitest";

import type { Span } from "@/lib/api";

import { promptChange, systemPromptOf } from "./system-prompt";

function span(overrides: Partial<Span>): Span {
  return {
    span_id: "s1",
    parent_span_id: null,
    kind: "llm",
    name: "chat",
    status: "ok",
    started_at: "2026-10-07T12:00:00Z",
    input: null,
    output: null,
    payload_omitted: false,
    ...overrides,
  } as unknown as Span;
}

describe("systemPromptOf", () => {
  it("reads OpenAI system messages", () => {
    const input = [
      { role: "system", content: "You are terse." },
      { role: "user", content: "Hi" },
    ];
    expect(systemPromptOf([span({ input })])).toBe("You are terse.");
  });

  it("reads the Anthropic system field, string or text blocks", () => {
    const messages = [{ role: "user", content: "Hi" }];
    expect(systemPromptOf([span({ input: { system: "Be kind.", messages } })])).toBe("Be kind.");
    const blocks = { system: [{ type: "text", text: "Rule one." }], messages };
    expect(systemPromptOf([span({ input: blocks })])).toBe("Rule one.");
  });

  it("uses the first LLM call with an input, by start time", () => {
    const later = span({
      span_id: "s2",
      started_at: "2026-10-07T12:00:05Z",
      input: [{ role: "system", content: "Later" }],
    });
    const earlier = span({ input: [{ role: "system", content: "Earlier" }] });
    const tool = span({
      span_id: "t",
      kind: "tool",
      input: "x",
      started_at: "2026-10-07T11:00:00Z",
    });
    expect(systemPromptOf([later, tool, earlier])).toBe("Earlier");
  });

  it("is null without a system prompt or a captured input", () => {
    expect(systemPromptOf([span({ input: [{ role: "user", content: "Hi" }] })])).toBeNull();
    expect(systemPromptOf([span({ input: null })])).toBeNull();
    expect(systemPromptOf([])).toBeNull();
  });
});

describe("promptChange", () => {
  it("compares against turn 1, ignoring surrounding whitespace", () => {
    expect(promptChange("A", "A\n")).toBe("unchanged");
    expect(promptChange("A", "B")).toBe("changed");
  });

  it("is null when either prompt is unknown", () => {
    expect(promptChange(null, "A")).toBeNull();
    expect(promptChange("A", null)).toBeNull();
  });
});
