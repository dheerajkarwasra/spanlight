import { describe, expect, it } from "vitest";

import { groupRetries } from "./span-retries";
import { selfTimeSegments } from "./span-self-time";
import { buildSpanTree, type SpanNode } from "./span-tree";
import { testSpan } from "./span-test-data";

function root(...childSpans: ReturnType<typeof testSpan>[]): SpanNode {
  return buildSpanTree([testSpan("root", null, 100, 100), ...childSpans])[0] as SpanNode;
}

describe("selfTimeSegments", () => {
  it("is the whole bar without children", () => {
    expect(selfTimeSegments(root())).toEqual([{ start: 0, end: 1 }]);
  });

  it("leaves out the union of overlapping children", () => {
    const segments = selfTimeSegments(
      root(
        testSpan("a", "root", 110, 30),
        testSpan("b", "root", 130, 20),
        testSpan("c", "root", 170, 10),
      ),
    );

    expect(segments).toEqual([
      { start: 0, end: 0.1 },
      { start: 0.5, end: 0.7 },
      { start: 0.8, end: 1 },
    ]);
  });

  it("clips children outside the span", () => {
    const segments = selfTimeSegments(
      root(testSpan("early", "root", 50, 70), testSpan("late", "root", 190, 500)),
    );

    expect(segments).toEqual([{ start: 0.2, end: 0.9 }]);
  });

  it("is the whole bar for a zero-length span", () => {
    const node = buildSpanTree([
      testSpan("root", null, 0, 0),
      testSpan("a", "root", 0, 0),
    ])[0] as SpanNode;
    expect(selfTimeSegments(node)).toEqual([{ start: 0, end: 1 }]);
  });

  it("counts a retry group's own children, not its earlier attempts", () => {
    const [group] = groupRetries(
      buildSpanTree([
        testSpan("a1", null, 0, 50, { kind: "llm", request_hash: "h" }),
        // Overlaps the last attempt, so counting it would cut the self time.
        testSpan("a2", null, 10, 100, { kind: "llm", request_hash: "h" }),
        testSpan("tool", "a2", 60, 50),
      ]),
    );
    expect(group?.attempts).toHaveLength(2);
    expect(selfTimeSegments(group as SpanNode)).toEqual([{ start: 0, end: 0.5 }]);
  });
});
