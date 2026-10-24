import { describe, expect, it } from "vitest";

import type { Span, SpanKind } from "@/lib/api";

import {
  barLayout,
  computeTimeline,
  formatTickLabel,
  niceTickStep,
  timelineTicks,
  ttftOffsetPercent,
} from "./span-timeline";
import {
  ancestorIds,
  buildSpanTree,
  failedSpanCount,
  findSpanNode,
  flattenSpanTree,
  healthyParentIds,
  kindsInTrace,
  llmInputCandidates,
  llmOutputCandidates,
  parentSpanIds,
  treeItemMeta,
} from "./span-tree";

const BASE = Date.parse("2026-10-07T12:00:00.000Z");

function makeSpan(
  spanId: string,
  parentSpanId: string | null,
  startOffsetMs: number,
  durationMs: number,
): Span {
  return {
    span_id: spanId,
    parent_span_id: parentSpanId,
    kind: "other",
    name: spanId,
    status: "ok",
    status_message: null,
    error_class: null,
    provider_error_type: null,
    provider_error_code: null,
    rate_limit_kind: null,
    started_at: new Date(BASE + startOffsetMs).toISOString(),
    ended_at: new Date(BASE + startOffsetMs + durationMs).toISOString(),
    duration_ms: durationMs,
    provider: null,
    model: null,
    input_tokens: null,
    output_tokens: null,
    cached_tokens: null,
    cache_write_tokens: null,
    cache_write_1h_tokens: null,
    service_tier: null,
    web_search_requests: null,
    cost_usd: null,
    fee_usd: null,
    pricing_version: null,
    time_to_first_token_ms: null,
    input: null,
    output: null,
    attributes: {},
    truncated: false,
    payload_omitted: false,
    finish_reason: null,
    prompt_version_id: null,
    usage_rollup: false,
    usage_state: "not_applicable",
    request_hash: null,
  };
}

describe("buildSpanTree", () => {
  it("nests children under parents and orders siblings by start time", () => {
    const spans = [
      makeSpan("child-late", "root", 50, 10),
      makeSpan("root", null, 0, 100),
      makeSpan("child-early", "root", 10, 10),
      makeSpan("grandchild", "child-early", 12, 2),
    ];

    const roots = buildSpanTree(spans);

    expect(roots).toHaveLength(1);
    const [root] = roots;
    expect(root?.span.span_id).toBe("root");
    expect(root?.children.map((node) => node.span.span_id)).toEqual(["child-early", "child-late"]);
    expect(root?.children[0]?.children.map((node) => node.span.span_id)).toEqual(["grandchild"]);
  });

  it("promotes spans with a missing parent to roots and flags them", () => {
    const spans = [makeSpan("root", null, 0, 100), makeSpan("orphan", "not-here", 5, 10)];

    const roots = buildSpanTree(spans);

    expect(roots.map((node) => node.span.span_id)).toEqual(["root", "orphan"]);
    expect(roots[0]?.orphaned).toBe(false);
    expect(roots[1]?.orphaned).toBe(true);
  });

  it("keeps every span exactly once when parents form a cycle", () => {
    const spans = [makeSpan("a", "b", 0, 10), makeSpan("b", "a", 1, 10), makeSpan("c", null, 2, 1)];

    const rows = flattenSpanTree(buildSpanTree(spans), new Set());

    expect(rows.map((row) => row.span.span_id).sort()).toEqual(["a", "b", "c"]);
    // Their parents exist, so none is flagged as missing one.
    expect(rows.some((row) => row.orphaned)).toBe(false);
  });

  it("treats a span that names itself as parent as a root", () => {
    const roots = buildSpanTree([makeSpan("self", "self", 0, 1)]);

    expect(roots.map((node) => node.span.span_id)).toEqual(["self"]);
    expect(roots[0]?.children).toEqual([]);
  });

  it("ignores duplicate span ids", () => {
    const roots = buildSpanTree([makeSpan("a", null, 0, 1), makeSpan("a", null, 0, 1)]);

    expect(roots).toHaveLength(1);
  });
});

describe("flattenSpanTree", () => {
  const spans = [
    makeSpan("root", null, 0, 100),
    makeSpan("a", "root", 10, 30),
    makeSpan("a1", "a", 12, 5),
    makeSpan("b", "root", 50, 30),
    makeSpan("b1", "b", 55, 5),
  ];

  it("lists rows depth-first with their depth", () => {
    const rows = flattenSpanTree(buildSpanTree(spans), new Set());

    expect(rows.map((row) => [row.span.span_id, row.depth])).toEqual([
      ["root", 0],
      ["a", 1],
      ["a1", 2],
      ["b", 1],
      ["b1", 2],
    ]);
    expect(rows.find((row) => row.span.span_id === "a1")).toMatchObject({
      hasChildren: false,
      expanded: true,
      orphaned: false,
    });
  });

  it("hides the descendants of collapsed spans", () => {
    const rows = flattenSpanTree(buildSpanTree(spans), new Set(["a"]));

    expect(rows.map((row) => row.span.span_id)).toEqual(["root", "a", "b", "b1"]);
    expect(rows.find((row) => row.span.span_id === "a")).toMatchObject({
      hasChildren: true,
      expanded: false,
    });
  });

  it("collects parent ids for collapse-all", () => {
    expect(parentSpanIds(buildSpanTree(spans)).sort()).toEqual(["a", "b", "root"]);
  });
});

describe("ancestorIds", () => {
  it("returns every node above the given spans in the forest, cycles broken", () => {
    const roots = buildSpanTree([
      makeSpan("root", null, 0, 10),
      makeSpan("a", "root", 1, 5),
      makeSpan("a1", "a", 2, 1),
      makeSpan("b", "root", 3, 1),
      makeSpan("x", "y", 0, 1),
      makeSpan("y", "x", 0, 1),
    ]);

    expect(ancestorIds(roots, new Set(["a1"]))).toEqual(new Set(["a", "root"]));
    expect(ancestorIds(roots, new Set(["a1", "b"]))).toEqual(new Set(["a", "root"]));
    expect(ancestorIds(roots, new Set(["root"]))).toEqual(new Set());
    expect(ancestorIds(roots, new Set(["y"]))).toEqual(new Set(["x"]));
  });
});

describe("computeTimeline", () => {
  it("spans from the earliest start to the latest end", () => {
    const timeline = computeTimeline([makeSpan("a", null, 100, 50), makeSpan("b", null, 0, 400)]);

    expect(timeline.startMs).toBe(BASE);
    expect(timeline.durationMs).toBe(400);
  });

  it("never reports a zero duration", () => {
    expect(computeTimeline([makeSpan("a", null, 0, 0)]).durationMs).toBe(1);
    expect(computeTimeline([]).durationMs).toBe(1);
  });
});

describe("barLayout", () => {
  const timeline = computeTimeline([makeSpan("root", null, 0, 1000)]);

  it("positions bars proportionally to start offset and duration", () => {
    expect(barLayout(makeSpan("a", "root", 250, 500), timeline)).toEqual({
      offsetPercent: 25,
      widthPercent: 50,
    });
  });

  it("gives tiny spans a minimum visible width", () => {
    const layout = barLayout(makeSpan("tiny", "root", 100, 0), timeline);

    expect(layout.widthPercent).toBe(0.5);
    expect(layout.offsetPercent).toBe(10);
  });

  it("keeps a minimum-width bar at the very end inside the track", () => {
    const layout = barLayout(makeSpan("end", "root", 1000, 0), timeline);

    expect(layout.offsetPercent + layout.widthPercent).toBeLessThanOrEqual(100);
  });
});

describe("timeline ticks", () => {
  it("chooses 1/2/5 steps", () => {
    expect(niceTickStep(1000)).toBe(200);
    expect(niceTickStep(900)).toBe(200);
    expect(niceTickStep(2400)).toBe(500);
    expect(niceTickStep(7)).toBe(2);
  });

  it("starts at zero and stays within the duration", () => {
    const ticks = timelineTicks(1000);

    expect(ticks.map((tick) => tick.ms)).toEqual([0, 200, 400, 600, 800, 1000]);
    expect(ticks.at(-1)?.percent).toBe(100);
  });

  it("handles a degenerate duration", () => {
    expect(timelineTicks(1).map((tick) => tick.ms)).toEqual([0, 1]);
    expect(timelineTicks(3).map((tick) => tick.ms)).toEqual([0, 1, 2, 3]);
  });
});

describe("formatTickLabel", () => {
  it("writes compact labels for round tick values", () => {
    expect(formatTickLabel(0)).toBe("0");
    expect(formatTickLabel(500)).toBe("500ms");
    expect(formatTickLabel(1000)).toBe("1s");
    expect(formatTickLabel(1500)).toBe("1.5s");
    expect(formatTickLabel(20_000)).toBe("20s");
    expect(formatTickLabel(60_000)).toBe("1m");
    expect(formatTickLabel(80_000)).toBe("1m 20s");
  });

  it("falls back to zero for invalid input", () => {
    expect(formatTickLabel(Number.NaN)).toBe("0");
    expect(formatTickLabel(-5)).toBe("0");
  });
});

describe("ttftOffsetPercent", () => {
  it("places the marker proportionally inside the bar", () => {
    const span = { ...makeSpan("llm", null, 0, 1000), time_to_first_token_ms: 250 };
    expect(ttftOffsetPercent(span)).toBe(25);
  });

  it("clamps a TTFT longer than the span to the bar end", () => {
    const span = { ...makeSpan("llm", null, 0, 100), time_to_first_token_ms: 400 };
    expect(ttftOffsetPercent(span)).toBe(100);
  });

  it("is null when TTFT is unknown or the span has no duration", () => {
    expect(ttftOffsetPercent(makeSpan("llm", null, 0, 100))).toBeNull();
    const instant = { ...makeSpan("llm", null, 0, 0), time_to_first_token_ms: 10 };
    expect(ttftOffsetPercent(instant)).toBeNull();
  });
});

describe("kindsInTrace and failedSpanCount", () => {
  function withKind(spanId: string, kind: SpanKind, status: Span["status"] = "ok"): Span {
    return { ...makeSpan(spanId, null, 0, 10), kind, status };
  }

  it("lists the kinds present in legend order", () => {
    const spans = [withKind("a", "http"), withKind("b", "llm"), withKind("c", "http")];
    expect(kindsInTrace(spans)).toEqual(["llm", "http"]);
  });

  it("counts failed spans", () => {
    const spans = [
      withKind("a", "tool", "error"),
      withKind("b", "llm"),
      withKind("c", "http", "error"),
    ];
    expect(failedSpanCount(spans)).toBe(2);
  });
});

describe("healthyParentIds", () => {
  it("lists the parents whose subtree has no failed span", () => {
    const spans: Span[] = [
      makeSpan("root", null, 0, 100),
      makeSpan("ok-parent", "root", 1, 10),
      makeSpan("ok-leaf", "ok-parent", 2, 5),
      makeSpan("error-path", "root", 20, 10),
      { ...makeSpan("error-leaf", "error-path", 21, 5), status: "error" },
      { ...makeSpan("failed-parent", "root", 40, 10), status: "error" },
      makeSpan("under-failed", "failed-parent", 41, 5),
    ];
    expect(healthyParentIds(buildSpanTree(spans))).toEqual(new Set(["ok-parent"]));
  });
});

describe("treeItemMeta", () => {
  it("gives each span its position among siblings and how many spans sit under it", () => {
    const roots = buildSpanTree([
      makeSpan("root", null, 0, 100),
      makeSpan("a", "root", 1, 10),
      makeSpan("a1", "a", 2, 2),
      makeSpan("a2", "a", 3, 2),
      makeSpan("b", "root", 20, 10),
    ]);
    const meta = treeItemMeta(roots);
    expect(meta.get("root")).toEqual({ posInSet: 1, setSize: 1, descendants: 4 });
    expect(meta.get("a")).toEqual({ posInSet: 1, setSize: 2, descendants: 2 });
    expect(meta.get("b")).toEqual({ posInSet: 2, setSize: 2, descendants: 0 });
    expect(meta.get("a2")).toEqual({ posInSet: 2, setSize: 2, descendants: 0 });
  });
});

describe("llmInputCandidates and llmOutputCandidates", () => {
  function llm(id: string, parent: string, start: number, io: Partial<Span> = {}): Span {
    return { ...makeSpan(id, parent, start, 5), kind: "llm", ...io };
  }

  const spans: Span[] = [
    makeSpan("root", null, 0, 100),
    makeSpan("plan", "root", 1, 30),
    llm("plan-llm", "plan", 2, { input: [{ role: "user", content: "a" }], output: "x" }),
    llm("no-io", "root", 40),
    llm("answer", "root", 50, { payload_omitted: true }),
    makeSpan("tool", "root", 60, 5),
  ];
  const roots = buildSpanTree(spans);
  const rootChildren = findSpanNode(roots, "root")?.children ?? [];

  it("lists LLM spans with an input depth first, skipping ones known to have none", () => {
    expect(llmInputCandidates(rootChildren).map((span) => span.span_id)).toEqual([
      "plan-llm",
      "answer",
    ]);
  });

  it("lists LLM spans with an output last first; an omitted payload is still a candidate", () => {
    expect(llmOutputCandidates(rootChildren).map((span) => span.span_id)).toEqual([
      "answer",
      "plan-llm",
    ]);
  });

  it("is empty when no LLM span carries a payload", () => {
    const bare = buildSpanTree([makeSpan("root", null, 0, 10), llm("empty", "root", 1)]);
    const children = findSpanNode(bare, "root")?.children ?? [];
    expect(llmInputCandidates(children)).toEqual([]);
    expect(llmOutputCandidates(children)).toEqual([]);
  });

  it("finds a nested node by id", () => {
    expect(findSpanNode(roots, "plan-llm")?.span.span_id).toBe("plan-llm");
    expect(findSpanNode(roots, "missing")).toBeNull();
  });
});
