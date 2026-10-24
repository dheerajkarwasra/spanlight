import { describe, expect, it } from "vitest";

import type { CompareItemSide, ItemDelta, ItemScore } from "@/lib/api";

import {
  categoryOrder,
  changeCounts,
  costCertainty,
  deltaTone,
  formatMean,
  formatMoneyDelta,
  formatMsDelta,
  formatScoreDelta,
  formatTradeOff,
  itemChange,
  regressionsOnly,
  scoreKind,
  sortByChange,
} from "./compare-view";

function side(scores: Record<string, ItemScore>): CompareItemSide {
  return { trace_id: null, output: null, scores, latency_ms: null, cost_usd: null, error: null };
}

function item(
  id: string,
  a: Record<string, ItemScore> | null,
  b: Record<string, ItemScore> | null,
  delta: number | null,
): ItemDelta {
  return {
    dataset_item_id: id,
    position: 1,
    input: null,
    expected_output: null,
    changed: null,
    a: a === null ? null : side(a),
    b: b === null ? null : side(b),
    deltas: { quality: delta },
  };
}

describe("score deltas", () => {
  it("keep the sign and enough decimals for a small change", () => {
    expect(formatScoreDelta(0.08, "numeric")).toBe("+0.08");
    expect(formatScoreDelta(-0.53, "numeric")).toBe("−0.53");
    expect(formatScoreDelta(0.003, "numeric")).toBe("+0.0030");
    expect(formatScoreDelta(0, "numeric")).toBe("0.00");
    expect(formatScoreDelta(null, "numeric")).toBeNull();
  });

  it("read a yes/no mean change in points, never a bare 0 pts for a real change", () => {
    expect(formatScoreDelta(0.06, "boolean")).toBe("+6 pts");
    expect(formatScoreDelta(-0.004, "boolean")).toBe("−<1 pt");
  });

  // Finding T8-04: a mean change below 0.00005 (possible: means of 4-decimal scores over many
  // items) was shown as "+0.0000", a signed zero.
  it("never shows a real numeric change as +0.0000", () => {
    expect(formatScoreDelta(0.00003, "numeric")).toBe("+<0.0001");
    expect(formatScoreDelta(-0.00003, "numeric")).toBe("−<0.0001");
    expect(formatScoreDelta(0.00005, "numeric")).toBe("+0.0001");
  });
});

describe("formatMean", () => {
  it("shows a yes/no mean as a percent and a missing mean as null", () => {
    expect(formatMean(0.96, "boolean")).toBe("96%");
    expect(formatMean(0.86666, "numeric")).toBe("0.8667");
    expect(formatMean(null, "numeric")).toBeNull();
  });

  // Finding T8-04: 199 passes of 200 rounded to "100%", which reads as every item passing. The
  // budget screen rounds down for the same reason ("99.6 % never reads 100 %").
  it("does not round a pass rate below 100% up to 100%", () => {
    expect(formatMean(0.995, "boolean")).toBe("99%");
    expect(formatMean(1, "boolean")).toBe("100%");
    expect(formatMean(0.29, "boolean")).toBe("29%");
  });
});

describe("money and latency deltas", () => {
  it("keep sub-cent changes visible and signed", () => {
    expect(formatMoneyDelta("0.012")).toBe("+$0.012");
    expect(formatMoneyDelta("-0.3")).toBe("−$0.30");
    expect(formatMoneyDelta("0.00004")).toBe("+<$0.0001");
    expect(formatMoneyDelta("-0.00004")).toBe("−<$0.0001");
    expect(formatMoneyDelta("0")).toBe("$0.00");
  });

  it("are null when unknown, never $0", () => {
    expect(formatMoneyDelta(null)).toBeNull();
    expect(formatMsDelta(null)).toBeNull();
  });

  it("read latency in ms or s with a sign", () => {
    expect(formatMsDelta(310)).toBe("+310 ms");
    expect(formatMsDelta(-1200)).toBe("−1.2 s");
  });
});

describe("tone", () => {
  it("treats higher scores as good and higher cost or latency as bad", () => {
    expect(deltaTone(0.1, true)).toBe("good");
    expect(deltaTone(0.1, false)).toBe("bad");
    expect(deltaTone(0, true)).toBe("neutral");
    expect(deltaTone(null, true)).toBe("neutral");
  });
});

describe("item changes", () => {
  it("cannot compare an item missing from a run or missing the score", () => {
    expect(itemChange(item("1", null, { quality: 1 }, null), "quality")).toBe("not_comparable");
    expect(itemChange(item("1", { other: 1 }, { quality: 1 }, null), "quality")).toBe(
      "not_comparable",
    );
  });

  it("compares categorical values by equality", () => {
    expect(itemChange(item("1", { quality: "good" }, { quality: "bad" }, null), "quality")).toBe(
      "changed",
    );
    expect(itemChange(item("1", { quality: "good" }, { quality: "good" }, null), "quality")).toBe(
      "unchanged",
    );
  });

  it("sorts the largest change first, then unchanged, then not comparable, stable on ties", () => {
    const items = [
      item("missing", null, { quality: 1 }, null),
      item("same", { quality: 1 }, { quality: 1 }, 0),
      item("small", { quality: 0.5 }, { quality: 0.6 }, 0.1),
      item("big-drop", { quality: 1 }, { quality: 0.2 }, -0.8),
      item("small-2", { quality: 0.5 }, { quality: 0.6 }, 0.1),
    ];
    expect(sortByChange(items, "quality").map((entry) => entry.dataset_item_id)).toEqual([
      "big-drop",
      "small",
      "small-2",
      "same",
      "missing",
    ]);
    expect(changeCounts(items, "quality")).toEqual({
      changed: 3,
      unchanged: 1,
      notComparable: 1,
      scoredBoth: 4,
      inBoth: 4,
    });
  });
});

describe("regressionsOnly", () => {
  it("keeps numeric decreases and yes to no, drops gains, ties and items that cannot be compared", () => {
    const items = [
      item("drop", { quality: 0.9 }, { quality: 0.4 }, -0.5),
      item("gain", { quality: 0.4 }, { quality: 0.9 }, 0.5),
      item("same", { quality: 1 }, { quality: 1 }, 0),
      item("yes-no", { quality: true }, { quality: false }, -1),
      item("no-yes", { quality: false }, { quality: true }, 1),
      item("missing", null, { quality: 1 }, null),
      { ...item("edited", { quality: 1 }, { quality: 0 }, -1), changed: true },
    ];
    expect(regressionsOnly(items, "quality").map((entry) => entry.dataset_item_id)).toEqual([
      "drop",
      "yes-no",
    ]);
  });
});

describe("scoreKind", () => {
  it("prefers the config, else infers from the values", () => {
    expect(scoreKind("ok", undefined, [true, false])).toBe("boolean");
    expect(scoreKind("label", undefined, ["good"])).toBe("categorical");
    expect(scoreKind("q", undefined, [0.5])).toBe("numeric");
  });
});

describe("categoryOrder", () => {
  it("keeps the config's order, best first, for the categories that occur", () => {
    expect(categoryOrder(["good", "ok", "bad"], [{ bad: 2, good: 1 }, null])).toEqual([
      { name: "good", tone: "success" },
      { name: "bad", tone: "danger" },
    ]);
  });

  it("orders categories without a config by how often they occur", () => {
    expect(categoryOrder([], [{ b: 1, a: 3 }, { c: 2 }]).map((entry) => entry.name)).toEqual([
      "a",
      "c",
      "b",
    ]);
  });
});

describe("formatTradeOff", () => {
  it("states each part and says when cost or latency is unknown", () => {
    expect(
      formatTradeOff({
        score: "relevance",
        kind: "numeric",
        scoreDelta: 0.08,
        costDelta: "0.012",
        costCertainty: "exact",
        latencyP95Delta: 310,
      }),
    ).toBe("B scores +0.08 on relevance, costs +$0.012, p95 latency +310 ms.");
    expect(
      formatTradeOff({
        score: null,
        kind: "numeric",
        scoreDelta: null,
        costDelta: null,
        costCertainty: "exact",
        latencyP95Delta: null,
      }),
    ).toBe("B cost unknown, p95 latency unknown.");
  });

  it("never states a cost direction when run A has unpriced calls", () => {
    expect(costCertainty(true, false)).toBe("partial");
    expect(costCertainty(false, true)).toBe("at_least");
    const line = formatTradeOff({
      score: null,
      kind: "numeric",
      scoreDelta: null,
      costDelta: "-0.5",
      costCertainty: costCertainty(true, false),
      latencyP95Delta: 0,
    });
    expect(line).toBe("B cost partly unknown, p95 latency 0 ms.");
  });
});
