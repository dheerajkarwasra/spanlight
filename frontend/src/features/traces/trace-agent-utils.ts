import type { Price, Span } from "@/lib/api";

import { stepText } from "./compare-diff";
import { timeOf } from "./span-timeline";
import type { SpanNode } from "./span-tree";

export interface TopologyNode {
  span: Span;
  x: number;
  y: number;
  depth: number;
  lane: number;
}

export interface TopologyEdge {
  fromId: string;
  toId: string;
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}

export interface TopologyLayout {
  nodes: TopologyNode[];
  edges: TopologyEdge[];
  width: number;
  height: number;
  nodeWidth: number;
  nodeHeight: number;
}

const NODE_WIDTH = 208;
const NODE_HEIGHT = 60;
const DEPTH_GAP = 48;
const LANE_GAP = 16;
const PADDING = 16;

/**
 * Arranges the span tree into non-overlapping left-to-right columns by depth and vertical lanes.
 * Subtrees reserve contiguous vertical lanes so crossing edges are minimized.
 */
export function buildTopologyLayout(roots: readonly SpanNode[]): TopologyLayout {
  if (roots.length === 0) {
    return {
      nodes: [],
      edges: [],
      width: PADDING * 2 + NODE_WIDTH,
      height: PADDING * 2 + NODE_HEIGHT,
      nodeWidth: NODE_WIDTH,
      nodeHeight: NODE_HEIGHT,
    };
  }

  let nextLane = 0;
  let maxDepth = 0;
  const placed = new Map<string, TopologyNode>();
  const rawEdges: Array<{ fromId: string; toId: string }> = [];

  function placeSubtree(node: SpanNode, depth: number): { minLane: number; maxLane: number } {
    maxDepth = Math.max(maxDepth, depth);
    if (node.children.length === 0) {
      const lane = nextLane++;
      placed.set(node.span.span_id, {
        span: node.span,
        depth,
        lane,
        x: PADDING + depth * (NODE_WIDTH + DEPTH_GAP),
        y: PADDING + lane * (NODE_HEIGHT + LANE_GAP),
      });
      return { minLane: lane, maxLane: lane };
    }

    let minLane = Number.POSITIVE_INFINITY;
    let maxLane = -1;
    for (const child of node.children) {
      rawEdges.push({ fromId: node.span.span_id, toId: child.span.span_id });
      const bounds = placeSubtree(child, depth + 1);
      minLane = Math.min(minLane, bounds.minLane);
      maxLane = Math.max(maxLane, bounds.maxLane);
    }

    const centerLane = (minLane + maxLane) / 2;
    placed.set(node.span.span_id, {
      span: node.span,
      depth,
      lane: centerLane,
      x: PADDING + depth * (NODE_WIDTH + DEPTH_GAP),
      y: PADDING + centerLane * (NODE_HEIGHT + LANE_GAP),
    });
    return { minLane, maxLane };
  }

  for (const root of roots) {
    placeSubtree(root, 0);
  }

  const edges: TopologyEdge[] = [];
  for (const edge of rawEdges) {
    const parent = placed.get(edge.fromId);
    const child = placed.get(edge.toId);
    if (!parent || !child) continue;
    edges.push({
      fromId: edge.fromId,
      toId: edge.toId,
      x1: parent.x + NODE_WIDTH,
      y1: parent.y + NODE_HEIGHT / 2,
      x2: child.x,
      y2: child.y + NODE_HEIGHT / 2,
    });
  }

  const nodes = [...placed.values()];
  const totalLanes = Math.max(1, nextLane);
  return {
    nodes,
    edges,
    width: PADDING * 2 + (maxDepth + 1) * NODE_WIDTH + maxDepth * DEPTH_GAP,
    height: PADDING * 2 + totalLanes * NODE_HEIGHT + (totalLanes - 1) * LANE_GAP,
    nodeWidth: NODE_WIDTH,
    nodeHeight: NODE_HEIGHT,
  };
}

export interface PrefixComparison {
  prevSpanId: string;
  currSpanId: string;
  currName: string;
  model: string | null;
  commonPrefixChars: number;
  prevChars: number;
  currChars: number;
  prefixRatio: number;
  firstDiffLine: number | null;
  prevDivergence: string | null;
  currDivergence: string | null;
  cachedTokens: number | null;
  inputTokens: number | null;
}

/**
 * Compares each LLM call's formatted input against the preceding LLM call in the same trace to
 * pinpoint where an agent's prompt prefix diverges and breaks KV/prompt caching.
 */
export function computePrefixComparisons(spans: readonly Span[]): PrefixComparison[] {
  const llmSpans = spans
    .filter((span) => span.kind === "llm" && !span.usage_rollup && span.input !== null)
    .map((span) => ({ span, text: stepText(span.input), start: timeOf(span.started_at) }))
    .filter((entry) => entry.text.length > 0)
    .sort((a, b) => a.start - b.start || a.span.span_id.localeCompare(b.span.span_id));

  const comparisons: PrefixComparison[] = [];
  for (let idx = 1; idx < llmSpans.length; idx++) {
    const prev = llmSpans[idx - 1];
    const curr = llmSpans[idx];
    if (!prev || !curr) continue;

    const minChars = Math.min(prev.text.length, curr.text.length);
    let commonChars = 0;
    while (commonChars < minChars && prev.text[commonChars] === curr.text[commonChars]) {
      commonChars += 1;
    }

    const prevLines = prev.text.split("\n");
    const currLines = curr.text.split("\n");
    const maxLines = Math.max(prevLines.length, currLines.length);
    let firstDiffLine: number | null = null;
    let prevDivergence: string | null = null;
    let currDivergence: string | null = null;
    for (let lineIdx = 0; lineIdx < maxLines; lineIdx++) {
      if (prevLines[lineIdx] !== currLines[lineIdx]) {
        firstDiffLine = lineIdx + 1;
        prevDivergence = (prevLines[lineIdx] ?? "<EOF>").slice(0, 140);
        currDivergence = (currLines[lineIdx] ?? "<EOF>").slice(0, 140);
        break;
      }
    }

    comparisons.push({
      prevSpanId: prev.span.span_id,
      currSpanId: curr.span.span_id,
      currName: curr.span.name,
      model: curr.span.model,
      commonPrefixChars: commonChars,
      prevChars: prev.text.length,
      currChars: curr.text.length,
      prefixRatio: prev.text.length > 0 ? commonChars / prev.text.length : 0,
      firstDiffLine,
      prevDivergence,
      currDivergence,
      cachedTokens: curr.span.cached_tokens,
      inputTokens: curr.span.input_tokens,
    });
  }
  return comparisons;
}

export interface ModelSimulation {
  provider: string;
  model: string;
  estimatedUsd: number;
  deltaUsd: number | null;
  savingsPct: number | null;
}

export interface TraceTokenTotals {
  llmCalls: number;
  inputTokens: number;
  cachedTokens: number;
  outputTokens: number;
  actualUsd: number | null;
  feeUsd: number;
}

/** Summarizes billable non-rollup LLM token usage across the trace. */
export function summarizeTraceTokens(spans: readonly Span[]): TraceTokenTotals {
  let llmCalls = 0;
  let inputTokens = 0;
  let cachedTokens = 0;
  let outputTokens = 0;
  let actualUsdSum = 0;
  let hasAnyCost = false;
  let feeUsd = 0;

  for (const span of spans) {
    if (span.kind !== "llm" || span.usage_rollup) continue;
    llmCalls += 1;
    inputTokens += span.input_tokens ?? 0;
    cachedTokens += span.cached_tokens ?? 0;
    outputTokens += span.output_tokens ?? 0;
    if (span.cost_usd !== null) {
      const parsed = Number.parseFloat(span.cost_usd);
      if (Number.isFinite(parsed)) {
        actualUsdSum += parsed;
        hasAnyCost = true;
      }
    }
    if (span.fee_usd !== null) {
      const parsedFee = Number.parseFloat(span.fee_usd);
      if (Number.isFinite(parsedFee)) {
        feeUsd += parsedFee;
      }
    }
  }

  return {
    llmCalls,
    inputTokens,
    cachedTokens,
    outputTokens,
    actualUsd: hasAnyCost ? actualUsdSum : null,
    feeUsd,
  };
}

/** Estimates what this trace's LLM token volume would cost across models in `prices`. */
export function simulateTraceModels(
  spans: readonly Span[],
  prices: readonly Price[],
): { totals: TraceTokenTotals; models: ModelSimulation[] } {
  const totals = summarizeTraceTokens(spans);
  if (totals.inputTokens === 0 && totals.outputTokens === 0) {
    return { totals, models: [] };
  }

  const latestByModel = new Map<string, Price>();
  for (const price of prices) {
    const key = `${price.provider}:${price.model_pattern}`;
    const existing = latestByModel.get(key);
    if (!existing) {
      latestByModel.set(key, price);
      continue;
    }
    const existingBase = existing.min_prompt_tokens === 0;
    const candidateBase = price.min_prompt_tokens === 0;
    if (candidateBase && !existingBase) {
      latestByModel.set(key, price);
    } else if (candidateBase === existingBase && price.effective_from > existing.effective_from) {
      latestByModel.set(key, price);
    }
  }

  const uncachedInput = Math.max(0, totals.inputTokens - totals.cachedTokens);
  const models: ModelSimulation[] = [];
  for (const price of latestByModel.values()) {
    const inputRate = Number.parseFloat(price.input_per_mtok);
    const outputRate = Number.parseFloat(price.output_per_mtok);
    const cachedRate = price.cached_input_per_mtok
      ? Number.parseFloat(price.cached_input_per_mtok)
      : inputRate;
    if (!Number.isFinite(inputRate) || !Number.isFinite(outputRate)) continue;

    const estimatedUsd =
      totals.feeUsd +
      (uncachedInput * inputRate +
        totals.cachedTokens * (Number.isFinite(cachedRate) ? cachedRate : inputRate) +
        totals.outputTokens * outputRate) /
        1_000_000;

    const deltaUsd = totals.actualUsd !== null ? estimatedUsd - totals.actualUsd : null;
    const savingsPct =
      totals.actualUsd !== null && totals.actualUsd > 0 && deltaUsd !== null
        ? (-deltaUsd / totals.actualUsd) * 100
        : null;

    models.push({
      provider: price.provider,
      model: price.model_pattern,
      estimatedUsd,
      deltaUsd,
      savingsPct,
    });
  }

  models.sort((a, b) => a.estimatedUsd - b.estimatedUsd);
  return { totals, models };
}
