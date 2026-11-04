/**
 * Span tree shape: building, flattening and walking the forest. Pure functions only — no React here.
 * Timeline math lives in `span-timeline.ts`.
 */
import type { Span, SpanKind } from "@/lib/api";

import { timeOf } from "./span-timeline";
import { walkSpanTree } from "./span-traversal";

export interface SpanNode {
  span: Span;
  children: SpanNode[];
  /** True when the span names a parent that isn't in this trace (still arriving, or dropped). */
  orphaned: boolean;
  /** A collapsed retry group: every attempt, this (the last) one included. See `span-retries.ts`. */
  attempts?: SpanNode[];
}

/**
 * Spans ordered by start time, then span id. Each timestamp is parsed once: parsing in the
 * comparator cost a 5,000-span trace about 100,000 date parses.
 */
function sortBySpanStart(spans: readonly Span[]): Span[] {
  return spans
    .map((span) => ({ span, start: timeOf(span.started_at) }))
    .sort((a, b) => a.start - b.start || a.span.span_id.localeCompare(b.span.span_id))
    .map(({ span }) => span);
}

/**
 * Builds the span forest from `parent_span_id`. Spans whose parent is missing
 * become roots. Siblings are ordered by start time. Cycles (malformed data)
 * are broken so every span appears exactly once.
 */
export function buildSpanTree(spans: readonly Span[]): SpanNode[] {
  const byId = new Map<string, Span>();
  for (const span of spans) {
    if (!byId.has(span.span_id)) {
      byId.set(span.span_id, span);
    }
  }

  const childrenByParent = new Map<string, Span[]>();
  const rootSpans: Span[] = [];
  for (const span of byId.values()) {
    const parentId = span.parent_span_id;
    if (parentId !== null && parentId !== span.span_id && byId.has(parentId)) {
      const siblings = childrenByParent.get(parentId) ?? [];
      siblings.push(span);
      childrenByParent.set(parentId, siblings);
    } else {
      rootSpans.push(span);
    }
  }

  const placed = new Set<string>();

  function toNode(span: Span): SpanNode {
    const root: SpanNode = { span, children: [], orphaned: false };
    const stack = [root];
    while (stack.length > 0) {
      const node = stack.pop();
      if (!node) continue;
      if (placed.has(node.span.span_id)) continue;
      placed.add(node.span.span_id);
      node.orphaned = node.span.parent_span_id !== null && !byId.has(node.span.parent_span_id);
      const children = sortBySpanStart(childrenByParent.get(node.span.span_id) ?? []);
      for (const child of children) {
        if (!placed.has(child.span_id)) {
          node.children.push({ span: child, children: [], orphaned: false });
        }
      }
      for (const child of node.children.toReversed()) stack.push(child);
    }
    return root;
  }

  const roots = sortBySpanStart(rootSpans).map(toNode);

  // Spans caught in a parent cycle are unreachable from any root: surface them as roots.
  const unplaced = [...byId.values()].filter((span) => !placed.has(span.span_id));
  for (const span of sortBySpanStart(unplaced)) {
    if (!placed.has(span.span_id)) {
      // Its parent is in the trace, just unreachable: a root, but not an orphan.
      roots.push(toNode(span));
    }
  }

  return roots;
}

export interface SpanRow {
  span: Span;
  depth: number;
  hasChildren: boolean;
  expanded: boolean;
  orphaned: boolean;
}

/** Depth-first rows for the waterfall, skipping children of collapsed spans. */
export function flattenSpanTree(
  roots: readonly SpanNode[],
  collapsed: ReadonlySet<string>,
): SpanRow[] {
  return Array.from(walkSpanTree(roots, collapsed), ({ node, depth }) => ({
    span: node.span,
    depth,
    hasChildren: node.children.length > 0,
    expanded: !collapsed.has(node.span.span_id),
    orphaned: node.orphaned,
  }));
}

/** Every span id that has children — handy for "collapse all". */
export function parentSpanIds(roots: readonly SpanNode[]): string[] {
  return Array.from(walkSpanTree(roots))
    .filter(({ node }) => node.children.length > 0)
    .map(({ node }) => node.span.span_id);
}

/**
 * The ids of every node above any of `spanIds` in this forest (as shown: a retry group is the
 * parent of its earlier attempts). Used to reveal a selected span and search matches.
 */
export function ancestorIds(roots: readonly SpanNode[], spanIds: ReadonlySet<string>): Set<string> {
  const ancestors = new Set<string>();
  if (spanIds.size === 0) {
    return ancestors;
  }
  const parents = new Map<string, string | null>();
  const path: string[] = [];
  for (const { node, depth } of walkSpanTree(roots)) {
    parents.set(node.span.span_id, depth === 0 ? null : (path[depth - 1] ?? null));
    path[depth] = node.span.span_id;
  }
  for (const id of spanIds) {
    let parent = parents.get(id);
    while (parent !== null && parent !== undefined && !ancestors.has(parent)) {
      ancestors.add(parent);
      parent = parents.get(parent);
    }
  }
  return ancestors;
}

/** Legend order for span kinds. */
export const SPAN_KIND_ORDER: readonly SpanKind[] = [
  "llm",
  "tool",
  "retrieval",
  "chain",
  "agent",
  "http",
  "other",
];

/** The span kinds that occur in a trace, in legend order. */
export function kindsInTrace(spans: readonly Span[]): SpanKind[] {
  const present = new Set(spans.map((span) => span.kind));
  return SPAN_KIND_ORDER.filter((kind) => present.has(kind));
}

export function failedSpanCount(spans: readonly Span[]): number {
  return spans.filter((span) => span.status === "error").length;
}

/** Above this many spans the waterfall opens with healthy subtrees collapsed. */
export const AUTO_COLLAPSE_SPANS = 100;

/** Parents whose subtree (the parent included) has no failed span: the ones safe to fold away. */
export function healthyParentIds(roots: readonly SpanNode[]): Set<string> {
  const healthy = new Set<string>();
  const failed = new Set<SpanNode>();
  for (const { node } of Array.from(walkSpanTree(roots)).reverse()) {
    const ok = node.span.status !== "error" && !node.children.some((child) => failed.has(child));
    if (!ok) failed.add(node);
    else if (node.children.length > 0) healthy.add(node.span.span_id);
  }
  return healthy;
}

export interface TreeItemMeta {
  /** 1-based position among siblings (`aria-posinset`). */
  posInSet: number;
  /** Number of siblings, itself included (`aria-setsize`). */
  setSize: number;
  /** Spans below this one, at any depth: what a collapsed row hides. */
  descendants: number;
}

/**
 * Per-span tree position, so a virtualized waterfall can still announce where each row sits
 * when its siblings aren't in the DOM.
 */
export function treeItemMeta(roots: readonly SpanNode[]): Map<string, TreeItemMeta> {
  const meta = new Map<string, TreeItemMeta>();
  for (const { node, posInSet, setSize } of Array.from(walkSpanTree(roots)).reverse()) {
    let descendants = 0;
    for (const child of node.children)
      descendants += 1 + (meta.get(child.span.span_id)?.descendants ?? 0);
    meta.set(node.span.span_id, { posInSet, setSize, descendants });
  }
  return meta;
}

/** The node of `spanId` anywhere in the forest, or null. */
export function findSpanNode(roots: readonly SpanNode[], spanId: string): SpanNode | null {
  for (const { node } of walkSpanTree(roots)) {
    if (node.span.span_id === spanId) return node;
  }
  return null;
}

/** Spans in depth-first order (siblings by start time), as the waterfall lists them. */
function depthFirstSpans(roots: readonly SpanNode[]): Span[] {
  return Array.from(walkSpanTree(roots), ({ node }) => node.span);
}

/**
 * An LLM span that may carry this payload. An omitted payload is unknown until the span route
 * returns it (it can still be null), so it stays a candidate.
 */
function mayHavePayload(span: Span, side: "input" | "output"): boolean {
  return span.kind === "llm" && (span[side] !== null || span.payload_omitted);
}

/**
 * The LLM spans that may hold what a span without input was asked, best first: depth first.
 * The caller takes the first whose input turns out non-null.
 */
export function llmInputCandidates(roots: readonly SpanNode[]): Span[] {
  return depthFirstSpans(roots).filter((span) => mayHavePayload(span, "input"));
}

/** The LLM spans that may hold what a span without output answered, best first: last first. */
export function llmOutputCandidates(roots: readonly SpanNode[]): Span[] {
  return depthFirstSpans(roots)
    .filter((span) => mayHavePayload(span, "output"))
    .reverse();
}
