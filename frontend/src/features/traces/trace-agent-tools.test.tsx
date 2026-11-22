import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { Price } from "@/lib/api";

import { AgentTopologyGraph } from "./agent-topology-graph";
import { testSpan } from "./span-test-data";
import { buildSpanTree } from "./span-tree";
import {
  buildTopologyLayout,
  computePrefixComparisons,
  simulateTraceModels,
} from "./trace-agent-utils";

const PRICES: Price[] = [
  {
    provider: "openai",
    model_pattern: "gpt-4o",
    input_per_mtok: "2.50",
    output_per_mtok: "10.00",
    cached_input_per_mtok: "1.25",
    cache_write_5m_per_mtok: null,
    cache_write_1h_per_mtok: null,
    min_prompt_tokens: 0,
    effective_from: "2026-01-01T00:00:00Z",
    version: "v1",
  },
  {
    provider: "openai",
    model_pattern: "gpt-4o-mini",
    input_per_mtok: "0.15",
    output_per_mtok: "0.60",
    cached_input_per_mtok: "0.075",
    cache_write_5m_per_mtok: null,
    cache_write_1h_per_mtok: null,
    min_prompt_tokens: 0,
    effective_from: "2026-01-01T00:00:00Z",
    version: "v1",
  },
];

describe("buildTopologyLayout", () => {
  it("lays out parent-child spans cleanly across depth columns and connects edges", () => {
    const spans = [
      testSpan("agent-root", null, 0, 500, { kind: "agent", name: "planner" }),
      testSpan("llm-1", "agent-root", 10, 120, { kind: "llm", model: "gpt-4o" }),
      testSpan("tool-1", "agent-root", 140, 200, { kind: "tool", status: "error" }),
      testSpan("llm-2", "tool-1", 160, 100, { kind: "llm", model: "gpt-4o-mini" }),
    ];
    const layout = buildTopologyLayout(buildSpanTree(spans));
    expect(layout.nodes).toHaveLength(4);
    expect(layout.edges).toHaveLength(3);

    const byId = new Map(layout.nodes.map((node) => [node.span.span_id, node]));
    expect(byId.get("agent-root")?.depth).toBe(0);
    expect(byId.get("llm-1")?.depth).toBe(1);
    expect(byId.get("tool-1")?.depth).toBe(1);
    expect(byId.get("llm-2")?.depth).toBe(2);
    expect((byId.get("tool-1")?.y ?? 0) > (byId.get("llm-1")?.y ?? 0)).toBe(true);
  });
});

describe("AgentTopologyGraph", () => {
  it("renders nodes and selects the clicked span", () => {
    const spans = [
      testSpan("agent-root", null, 0, 500, { kind: "agent", name: "coordinator" }),
      testSpan("tool-call", "agent-root", 20, 100, {
        kind: "tool",
        name: "search_docs",
        status: "error",
      }),
    ];
    const onSelect = vi.fn();
    render(
      <AgentTopologyGraph
        roots={buildSpanTree(spans)}
        selectedSpanId="agent-root"
        onSelect={onSelect}
      />,
    );

    const toolBtn = screen.getByRole("button", { name: "Tool: search_docs (error)" });
    fireEvent.click(toolBtn);
    expect(onSelect).toHaveBeenCalledWith("tool-call");
  });
});

describe("computePrefixComparisons", () => {
  it("detects first diverging line when system prefix changes between LLM turns", () => {
    const spans = [
      testSpan("turn-1", null, 0, 100, {
        kind: "llm",
        input: "System: stable prompt\nTime: 10:00:00\nUser: hi",
        cached_tokens: 0,
      }),
      testSpan("turn-2", null, 200, 100, {
        kind: "llm",
        input: "System: stable prompt\nTime: 10:00:05\nUser: hi\nAssistant: ok",
        cached_tokens: 12,
      }),
      testSpan("rollup", null, 0, 300, {
        kind: "llm",
        input: "ignored rollup",
        usage_rollup: true,
      }),
    ];
    const comparisons = computePrefixComparisons(spans);
    expect(comparisons).toHaveLength(1);
    expect(comparisons[0]?.firstDiffLine).toBe(2);
    expect(comparisons[0]?.prevDivergence).toContain("Time: 10:00:00");
    expect(comparisons[0]?.currDivergence).toContain("Time: 10:00:05");
    expect(comparisons[0]?.prefixRatio).toBeGreaterThan(0.4);
    expect(comparisons[0]?.prefixRatio).toBeLessThan(1);
  });
});

describe("simulateTraceModels", () => {
  it("computes alternative model costs and savings against actual trace cost", () => {
    const spans = [
      testSpan("llm-1", null, 0, 100, {
        kind: "llm",
        input_tokens: 1_000_000,
        cached_tokens: 200_000,
        output_tokens: 100_000,
        cost_usd: "3.25",
      }),
    ];
    const result = simulateTraceModels(spans, PRICES);
    expect(result.totals.llmCalls).toBe(1);
    expect(result.models[0]?.model).toBe("gpt-4o-mini");
    // 800k uncached * 0.15/M (0.12) + 200k cached * 0.075/M (0.015) + 100k out * 0.60/M (0.06) = 0.195
    expect(result.models[0]?.estimatedUsd).toBeCloseTo(0.195, 6);
    expect(result.models[0]?.deltaUsd).toBeLessThan(0);
    expect(result.models[1]?.model).toBe("gpt-4o");
  });
});
