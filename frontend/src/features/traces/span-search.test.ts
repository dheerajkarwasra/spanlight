import { describe, expect, it } from "vitest";

import { filterTree, matchSpans } from "./span-search";
import { buildSpanTree, flattenSpanTree } from "./span-tree";
import { testSpan } from "./span-test-data";

describe("matchSpans", () => {
  const spans = [
    testSpan("root", null, 0, 100, { name: "answer_ticket" }),
    testSpan("llm", "root", 1, 10, { name: "chat", input: { q: "Where is my ORDER?" } }),
    testSpan("omitted", "root", 2, 10, { name: "chat", payload_omitted: true, output: "order" }),
    testSpan("late", "root", 3, 10, { output: ["x".repeat(20_000), "order"] }),
  ];

  it("matches names and loaded payloads, ignoring case", () => {
    expect(matchSpans(spans, "TICKET")).toEqual(new Set(["root"]));
    expect(matchSpans(spans, "order")).toEqual(new Set(["llm"]));
  });

  it("searches the payload's text, not its JSON", () => {
    const quoted = [
      testSpan("a", null, 0, 10, { output: { text: 'She said "hi"\nthen left' } }),
      testSpan("b", null, 0, 10, { input: [{ role: "user", content: "hello" }] }),
    ];
    expect(matchSpans(quoted, 'said "hi"')).toEqual(new Set(["a"]));
    expect(matchSpans(quoted, 'hi" then')).toEqual(new Set());
    expect(matchSpans(quoted, "role")).toEqual(new Set());
    expect(matchSpans(quoted, "text")).toEqual(new Set());
  });

  it("matches nothing for a blank query", () => {
    expect(matchSpans(spans, "  ")).toEqual(new Set());
  });
});

describe("filterTree", () => {
  it("keeps matching spans and their ancestors only", () => {
    const roots = buildSpanTree([
      testSpan("root", null, 0, 100),
      testSpan("chain", "root", 1, 50, { kind: "chain" }),
      testSpan("llm", "chain", 2, 10, { kind: "llm" }),
      testSpan("tool", "root", 60, 10, { kind: "tool" }),
    ]);

    const kept = filterTree(roots, (span) => span.kind === "llm");
    expect(flattenSpanTree(kept, new Set()).map((row) => row.span.span_id)).toEqual([
      "root",
      "chain",
      "llm",
    ]);
  });
});
