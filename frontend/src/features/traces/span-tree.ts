/**
 * Span tree shape: building, flattening and walking the forest. Pure functions only — no React here.
 * Timeline math lives in `span-timeline.ts`.
 */
import type { Span, SpanKind } from "@/lib/api";

import { timeOf } from "./span-timeline";

export interface SpanNode {
  span: Span;
  children: SpanNode[];
  /** True when the span names a parent that isn't in this trace (still arriving, or dropped). */
  orphaned: boolean;
  /** A collapsed retry group: every attempt, this (the last) one included. See `span-retries.ts`. */
  attempts?: SpanNode[];
}

function compareSpans(a: Span, b: Span): number {
  const byStart = timeOf(a.started_at) - timeOf(b.started_at);
  if (byStart !== 0) {
    return byStart;
  }
  return a.span_id.localeCompare(b.span_id);
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
    placed.add(span.span_id);
    const children = (childrenByParent.get(span.span_id) ?? [])
      .filter((child) => !placed.has(child.span_id))
      .sort(compareSpans)
      .map(toNode);
    const orphaned = span.parent_span_id !== null && !byId.has(span.parent_span_id);
    return { span, children, orphaned };
  }

  const roots = rootSpans.sort(compareSpans).map(toNode);

  // Spans caught in a parent cycle are unreachable from any root: surface them as roots.
  const unplaced = [...byId.values()].filter((span) => !placed.has(span.span_id));
  for (const span of unplaced.sort(compareSpans)) {
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
  const rows: SpanRow[] = [];

  function visit(node: SpanNode, depth: number): void {
    const expanded = !collapsed.has(node.span.span_id);
    rows.push({
      span: node.span,
      depth,
      hasChildren: node.children.length > 0,
      expanded,
      orphaned: node.orphaned,
    });
    if (!expanded) {
      return;
    }
    for (const child of node.children) {
      visit(child, depth + 1);
    }
  }

  for (const root of roots) {
    visit(root, 0);
  }
  return rows;
}

/** Every span id that has children — handy for "collapse all". */
export function parentSpanIds(roots: readonly SpanNode[]): string[] {
  const ids: string[] = [];
  const stack = [...roots];
  while (stack.length > 0) {
    const node = stack.pop();
    if (node && node.children.length > 0) {
      ids.push(node.span.span_id);
      stack.push(...node.children);
    }
  }
  return ids;
}

/**
 * The ids of every node above any of `spanIds` in this forest (as shown: a retry group is the
 * parent of its earlier attempts). Used to reveal a selected span and search matches.
 */
export function ancestorIds(roots: readonly SpanNode[], spanIds: ReadonlySet<string>): Set<string> {
  const ancestors = new Set<string>();
  function visit(node: SpanNode, path: string[]): void {
    if (spanIds.has(node.span.span_id)) {
      path.forEach((id) => ancestors.add(id));
    }
    const next = [...path, node.span.span_id];
    node.children.forEach((child) => {
      visit(child, next);
    });
  }
  roots.forEach((root) => {
    visit(root, []);
  });
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
  function visit(node: SpanNode): boolean {
    let ok = node.span.status !== "error";
    for (const child of node.children) {
      ok = visit(child) && ok;
    }
    if (ok && node.children.length > 0) {
      healthy.add(node.span.span_id);
    }
    return ok;
  }
  roots.forEach(visit);
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
  function visit(siblings: readonly SpanNode[]): number {
    let total = 0;
    siblings.forEach((node, index) => {
      const descendants = visit(node.children);
      meta.set(node.span.span_id, { posInSet: index + 1, setSize: siblings.length, descendants });
      total += descendants + 1;
    });
    return total;
  }
  visit(roots);
  return meta;
}

/** The node of `spanId` anywhere in the forest, or null. */
export function findSpanNode(roots: readonly SpanNode[], spanId: string): SpanNode | null {
  const stack = [...roots];
  while (stack.length > 0) {
    const node = stack.pop();
    if (node?.span.span_id === spanId) {
      return node;
    }
    if (node) {
      stack.push(...node.children);
    }
  }
  return null;
}

/** Spans in depth-first order (siblings by start time), as the waterfall lists them. */
function depthFirstSpans(roots: readonly SpanNode[]): Span[] {
  const spans: Span[] = [];
  const visit = (node: SpanNode) => {
    spans.push(node.span);
    node.children.forEach(visit);
  };
  roots.forEach(visit);
  return spans;
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
