import { describe, expect, it } from "vitest";

import { buildSpanTree, flattenSpanTree } from "./span-tree";
import { testSpan } from "./span-test-data";
import { topLevelPositions, waterfallItems } from "./waterfall-items";

describe("waterfallItems", () => {
  it("puts one ghost row above the first orphan of each missing parent", () => {
    const rows = flattenSpanTree(
      buildSpanTree([
        testSpan("root", null, 0, 100),
        testSpan("o1", "gone", 10, 10),
        testSpan("o2", "gone", 20, 10),
        testSpan("o3", "other", 30, 10),
      ]),
      new Set(),
    );

    expect(
      waterfallItems(rows).map((item) =>
        item.type === "ghost" ? `ghost:${item.parentSpanId}@${item.depth}` : item.row.span.span_id,
      ),
    ).toEqual(["root", "ghost:gone@0", "o1", "o2", "ghost:other@0", "o3"]);
    const items = waterfallItems(rows);
    expect([...topLevelPositions(items).values()]).toEqual([1, 2, 3, 4, 5, 6]);
  });

  it("adds no ghost for spans in a parent cycle, whose parents exist", () => {
    const rows = flattenSpanTree(
      buildSpanTree([testSpan("a", "b", 0, 10), testSpan("b", "a", 1, 10)]),
      new Set(),
    );
    expect(waterfallItems(rows).every((item) => item.type === "span")).toBe(true);
  });
});
