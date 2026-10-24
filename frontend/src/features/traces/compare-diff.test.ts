import { describe, expect, it } from "vitest";

import { lineDiff } from "@/lib/text-diff";

import { collapseUnchanged, stepDiff, stepText } from "./compare-diff";

describe("stepText", () => {
  it("renders chat messages as role and text, like the Messages tab", () => {
    const payload = [
      { role: "system", content: "Be brief." },
      { role: "user", content: "Where is my order?" },
    ];
    expect(stepText(payload)).toBe("System:\nBe brief.\n\nUser:\nWhere is my order?");
  });

  it("keeps a string as it is and pretty-prints other JSON", () => {
    expect(stepText("On its way.")).toBe("On its way.");
    expect(stepText({ order: 42 })).toBe('{\n  "order": 42\n}');
  });

  it("is empty when nothing was captured", () => {
    expect(stepText(null)).toBe("");
    expect(stepText(undefined)).toBe("");
  });
});

const lines = (count: number, prefix = "line") =>
  Array.from({ length: count }, (_, index) => `${prefix} ${String(index + 1)}`).join("\n");

describe("collapseUnchanged", () => {
  it("keeps three unchanged lines either side of a change and folds the rest", () => {
    const before = `${lines(10)}\nold\n${lines(10, "tail")}`;
    const after = `${lines(10)}\nnew\n${lines(10, "tail")}`;
    const items = collapseUnchanged(lineDiff(before, after));
    expect(
      items.map((item) => (item.kind === "gap" ? `… ${String(item.count)}` : item.text)),
    ).toEqual([
      "… 7",
      "line 8",
      "line 9",
      "line 10",
      "old",
      "new",
      "tail 1",
      "tail 2",
      "tail 3",
      "… 7",
    ]);
  });

  it("folds nothing in a short run", () => {
    const items = collapseUnchanged(lineDiff("a\nb\nold\nc", "a\nb\nnew\nc"));
    expect(items.some((item) => item.kind === "gap")).toBe(false);
  });

  it("keeps the first three lines of a text that did not change", () => {
    const items = collapseUnchanged(lineDiff(lines(8), lines(8)));
    expect(items.map((item) => (item.kind === "gap" ? item.count : item.text))).toEqual([
      "line 1",
      "line 2",
      "line 3",
      5,
    ]);
  });
});

describe("stepDiff", () => {
  // 1 KB lines, so the 16 KB cut lands mid-line.
  const kbLines = (from: number, count: number) =>
    Array.from(
      { length: count },
      (_, index) => `${String(from + index).padStart(4, "0")}${"y".repeat(1019)}`,
    ).join("\n");

  it("cuts both sides to the same preview when one passes the guard", () => {
    const shared = kbLines(0, 20);
    const result = stepDiff(`${shared}\n${kbLines(20, 50)}`, shared);
    expect(result.cut).toBe(true);
    expect(result.items.some((item) => item.kind === "added" || item.kind === "removed")).toBe(
      false,
    );
  });

  it("diffs whole texts under the guard", () => {
    const result = stepDiff("a\nold", "a\nnew");
    expect(result.cut).toBe(false);
    expect(result.items.map((item) => item.kind)).toEqual(["same", "removed", "added"]);
  });
});

describe("stepDiff past the payload guard", () => {
  const body = Array.from(
    { length: 70 },
    (_, index) => `${String(index)} ${"z".repeat(1000)}`,
  ).join("\n");

  it("cuts both sides at whole lines, so only the real change shows", () => {
    // The first lines differ in length, so a 16 KB cut lands at a different point in each line.
    const { items, cut } = stepDiff(`old\n${body}`, `newer\n${body}`);
    expect(cut).toBe(true);
    expect(
      items
        .filter((item) => item.kind === "removed")
        .map((item) => item.kind === "removed" && item.text),
    ).toEqual(["old"]);
    expect(
      items
        .filter((item) => item.kind === "added")
        .map((item) => item.kind === "added" && item.text),
    ).toEqual(["newer"]);
  });

  it("diffs whole texts under the guard", () => {
    expect(stepDiff("a\nb", "a\nc").cut).toBe(false);
  });
});
