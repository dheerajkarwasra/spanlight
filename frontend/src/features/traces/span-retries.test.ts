import { describe, expect, it } from "vitest";

import { groupRetries, retryCounts, retryGroupIds } from "./span-retries";
import { buildSpanTree, type SpanNode } from "./span-tree";
import { testSpan } from "./span-test-data";

function call(id: string, start: number, hash: string | null, failed = false) {
  return testSpan(id, "root", start, 10, {
    kind: "llm",
    request_hash: hash,
    status: failed ? "error" : "ok",
  });
}

function children(...spans: ReturnType<typeof call>[]): SpanNode[] {
  return buildSpanTree([testSpan("root", null, 0, 100), ...spans])[0]?.children ?? [];
}

const ids = (nodes: SpanNode[]) => nodes.map((node) => node.span.span_id);

describe("groupRetries", () => {
  it("folds consecutive siblings with one request hash into the last attempt", () => {
    const grouped = groupRetries(
      children(call("a1", 1, "h", true), call("a2", 20, "h", true), call("a3", 40, "h")),
    );

    expect(ids(grouped)).toEqual(["a3"]);
    expect(ids(grouped[0]?.attempts ?? [])).toEqual(["a1", "a2", "a3"]);
    // Expanding the group shows the earlier attempts.
    expect(ids(grouped[0]?.children ?? [])).toEqual(["a1", "a2"]);
    expect(retryCounts(grouped[0] as SpanNode)).toEqual({ attempts: 3, failed: 2 });
    expect(retryGroupIds(grouped)).toEqual(["a3"]);
  });

  it("breaks a group at a different hash and never groups null hashes", () => {
    const grouped = groupRetries(
      children(
        call("a1", 1, "h"),
        call("b", 10, "other"),
        call("a2", 20, "h"),
        call("n1", 30, null),
        call("n2", 40, null),
      ),
    );

    expect(ids(grouped)).toEqual(["a1", "b", "a2", "n1", "n2"]);
    expect(grouped.every((node) => node.attempts === undefined)).toBe(true);
    expect(retryGroupIds(grouped)).toEqual([]);
    expect(retryCounts(grouped[0] as SpanNode)).toBeNull();
  });

  it("leaves out what traces.retry_calls leaves out: cache hits, faults, rollups, non-LLM", () => {
    const hit = (id: string, start: number) => ({
      ...call(id, start, "h"),
      attributes: { "spanlight.cache": "hit" },
    });
    const tool = (id: string, start: number) => ({
      ...call(id, start, "t"),
      kind: "tool" as const,
    });
    const fault = (id: string, start: number) => ({
      ...call(id, start, "f"),
      attributes: { "spanlight.fault.scenario": "timeout" },
    });
    const rollup = (id: string, start: number) => ({ ...call(id, start, "r"), usage_rollup: true });
    const grouped = groupRetries(
      children(
        hit("c1", 1),
        call("c2", 5, "h"),
        tool("t1", 10),
        tool("t2", 20),
        fault("f1", 30),
        fault("f2", 40),
        rollup("r1", 50),
        rollup("r2", 60),
      ),
    );

    expect(retryGroupIds(grouped)).toEqual([]);
  });

  it("groups retries at every depth", () => {
    const roots = buildSpanTree([
      testSpan("root", null, 0, 100),
      testSpan("tool", "root", 1, 50),
      testSpan("c1", "tool", 2, 5, { kind: "llm", request_hash: "h" }),
      testSpan("c2", "tool", 10, 5, { kind: "llm", request_hash: "h" }),
    ]);

    const tool = groupRetries(roots)[0]?.children[0];
    expect(ids(tool?.children ?? [])).toEqual(["c2"]);
  });
});
