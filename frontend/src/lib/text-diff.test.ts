import { describe, expect, it } from "vitest";

import { countChanges, lineDiff } from "@/lib/text-diff";

describe("lineDiff", () => {
  it("shows a changed line as a removal then an addition, with numbers on each side", () => {
    expect(lineDiff("a\nb\nc", "a\nB\nc")).toEqual([
      { kind: "same", text: "a", oldNumber: 1, newNumber: 1 },
      { kind: "removed", text: "b", oldNumber: 2, newNumber: null },
      { kind: "added", text: "B", oldNumber: null, newNumber: 2 },
      { kind: "same", text: "c", oldNumber: 3, newNumber: 3 },
    ]);
  });

  it("keeps blank lines and counts every added and removed line", () => {
    const lines = lineDiff("one\n\nthree\n", "one\n\nthree\nfour\nfive\n");
    expect(lines.map((line) => line.text)).toEqual(["one", "", "three", "four", "five"]);
    expect(countChanges(lines)).toEqual({ added: 2, removed: 0 });
  });

  it("reports no change for identical texts", () => {
    expect(countChanges(lineDiff("same\ntext", "same\ntext"))).toEqual({ added: 0, removed: 0 });
  });
});
