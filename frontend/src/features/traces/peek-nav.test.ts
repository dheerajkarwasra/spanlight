import { describe, expect, it } from "vitest";

import { stepPeek } from "./peek-nav";

const IDS = ["t1", "t2", "t3"];

describe("stepPeek", () => {
  it.each([
    ["t1", 1, "t2"],
    ["t2", 1, "t3"],
    ["t2", -1, "t1"],
    ["t3", -1, "t2"],
    // The ends: no wrap, no "Load more".
    ["t3", 1, null],
    ["t1", -1, null],
    // The peeked trace left the list (filters changed, the list refetched): stay put.
    ["gone", 1, null],
    ["gone", -1, null],
  ] as const)("from %s by %i is %s", (current, delta, expected) => {
    expect(stepPeek(IDS, current, delta)).toBe(expected);
  });

  it("does nothing on an empty list", () => {
    expect(stepPeek([], "t1", 1)).toBeNull();
  });
});
