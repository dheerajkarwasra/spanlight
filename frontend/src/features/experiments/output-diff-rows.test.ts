import { describe, expect, it } from "vitest";

import { lineDiff } from "@/lib/text-diff";

import { alignDiff } from "./output-diff-rows";

const lines = (count: number, from = 1) =>
  Array.from({ length: count }, (_, index) => `line ${String(from + index)}`);

describe("alignDiff", () => {
  it("pairs removed and added lines on one row, padding the shorter side", () => {
    const rows = alignDiff(lineDiff("keep\nold 1\nold 2\nend", "keep\nnew 1\nnew 2\nnew 3\nend"));
    expect(rows).toEqual([
      { kind: "same", a: "keep", b: "keep", aNumber: 1, bNumber: 1 },
      { kind: "changed", a: "old 1", b: "new 1", aNumber: 2, bNumber: 2 },
      { kind: "changed", a: "old 2", b: "new 2", aNumber: 3, bNumber: 3 },
      { kind: "changed", a: null, b: "new 3", aNumber: null, bNumber: 4 },
      { kind: "same", a: "end", b: "end", aNumber: 4, bNumber: 5 },
    ]);
  });

  it("collapses an unchanged run longer than 6 lines to 3, a count and 3", () => {
    const before = [...lines(10), "old"].join("\n");
    const after = [...lines(10), "new"].join("\n");
    const rows = alignDiff(lineDiff(before, after));
    expect(rows.map((row) => row.kind)).toEqual([
      "same",
      "same",
      "same",
      "collapsed",
      "same",
      "same",
      "same",
      "changed",
    ]);
    expect(rows[3]).toEqual({ kind: "collapsed", a: null, b: null, count: 4 });
    expect(rows[4]).toMatchObject({ a: "line 8", aNumber: 8, bNumber: 8 });
  });

  it("keeps a run of exactly 6 unchanged lines whole", () => {
    const rows = alignDiff(
      lineDiff([...lines(6), "old"].join("\n"), [...lines(6), "new"].join("\n")),
    );
    expect(rows.filter((row) => row.kind === "same")).toHaveLength(6);
    expect(rows.some((row) => row.kind === "collapsed")).toBe(false);
  });
});
