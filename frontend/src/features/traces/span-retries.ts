/**
 * Retry groups (spec ruling 7): consecutive sibling spans with the same non-null `request_hash`,
 * the rule `traces.retry_calls` counts by. Pure functions.
 */
import type { Span } from "@/lib/api";

import type { SpanNode } from "./span-tree";
import { walkSpanTree } from "./span-traversal";

/**
 * The request hash of a span `traces.retry_calls` considers (`retry_eligible`, computed by the
 * server from the stored attributes, which trace detail may leave out). Null when the span
 * can't be an attempt.
 */
function retryKey(span: Span): string | null {
  return span.retry_eligible ? span.request_hash : null;
}

/**
 * Folds each run of consecutive siblings (already ordered by start) sharing a request hash into
 * the last attempt's node: `attempts` holds every attempt, and the earlier ones become its first
 * children, so expanding the group shows them. Applied at every depth.
 */
export function groupRetries(siblings: readonly SpanNode[]): SpanNode[] {
  const groupedChildren = new Map<SpanNode, SpanNode[]>();
  for (const { node } of Array.from(walkSpanTree(siblings)).reverse()) {
    groupedChildren.set(node, groupSiblings(node.children, groupedChildren));
  }
  return groupSiblings(siblings, groupedChildren);
}

function groupSiblings(siblings: readonly SpanNode[], children: ReadonlyMap<SpanNode, SpanNode[]>) {
  const nodes = siblings.map((node) => ({ ...node, children: children.get(node) ?? [] }));
  const grouped: SpanNode[] = [];
  let run: SpanNode[] = [];
  const flush = () => {
    const last = run.at(-1);
    if (last && run.length > 1) {
      grouped.push({ ...last, children: [...run.slice(0, -1), ...last.children], attempts: run });
    } else if (last) {
      grouped.push(last);
    }
    run = [];
  };
  for (const node of nodes) {
    const hash = retryKey(node.span);
    const runHash = run[0] ? retryKey(run[0].span) : null;
    if (hash === null || hash !== runHash) {
      flush();
    }
    run.push(node);
  }
  flush();
  return grouped;
}

/** A node's own child spans: a retry group's earlier attempts are shown under it, not its children. */
export function ownChildren(node: SpanNode): SpanNode[] {
  return node.attempts ? node.children.slice(node.attempts.length - 1) : node.children;
}

/** The pill's numbers for a retry group; null for any other node. */
export function retryCounts(node: SpanNode): { attempts: number; failed: number } | null {
  if (!node.attempts) {
    return null;
  }
  const failed = node.attempts.filter((attempt) => attempt.span.status === "error").length;
  return { attempts: node.attempts.length, failed };
}

/** The ids of every retry group in the forest (empty when the trace has no retries). */
export function retryGroupIds(nodes: readonly SpanNode[]): string[] {
  return Array.from(walkSpanTree(nodes))
    .filter(({ node }) => node.attempts !== undefined)
    .map(({ node }) => node.span.span_id);
}
