import { useMemo, useState } from "react";

import type { Span, SpanKind } from "@/lib/api";

import { doctorSpanId } from "./doctor-span";
import { groupRetries, retryGroupIds } from "./span-retries";
import { filterTree, matchSpans } from "./span-search";
import {
  AUTO_COLLAPSE_SPANS,
  ancestorIds,
  flattenSpanTree,
  healthyParentIds,
  parentSpanIds,
  treeItemMeta,
  type SpanNode,
} from "./span-tree";
import { topLevelPositions, waterfallItems } from "./waterfall-items";

interface Options {
  spans: Span[];
  roots: SpanNode[];
  selectedSpanId: string | null;
  onSelect: (spanId: string) => void;
  maxDepth?: number;
}

function indexNodes(nodes: readonly SpanNode[], byId = new Map<string, SpanNode>()) {
  for (const node of nodes) {
    byId.set(node.span.span_id, node);
    indexNodes(node.children, byId);
  }
  return byId;
}

/**
 * Parents folded on open: retry groups (one row each), healthy subtrees of a big trace (so the
 * failed path shows first), and everything from `maxDepth` levels down.
 */
function initialCollapsed(grouped: SpanNode[], spanCount: number, maxDepth?: number) {
  const ids = new Set(spanCount > AUTO_COLLAPSE_SPANS ? healthyParentIds(grouped) : []);
  retryGroupIds(grouped).forEach((id) => ids.add(id));
  if (maxDepth !== undefined) {
    flattenSpanTree(grouped, new Set())
      .filter((row) => row.hasChildren && row.depth >= maxDepth - 1)
      .forEach((row) => ids.add(row.span.span_id));
  }
  return ids;
}

/** The waterfall's shown tree: retries grouped, kind filter and search applied, folds kept. */
export function useWaterfallTree({ spans, roots, selectedSpanId, onSelect, maxDepth }: Options) {
  const grouped = useMemo(() => groupRetries(roots), [roots]);
  const hasRetries = useMemo(() => retryGroupIds(grouped).length > 0, [grouped]);
  const [collapseRetries, setCollapseRetries] = useState(true);
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(() =>
    initialCollapsed(grouped, spans.length, maxDepth),
  );
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState<SpanKind | null>(null);

  const tree = collapseRetries ? grouped : roots;
  const shown = useMemo(
    () => (kind ? filterTree(tree, (span) => span.kind === kind) : tree),
    [tree, kind],
  );
  const matches = useMemo(() => matchSpans(spans, query), [spans, query]);
  // A selected span (e.g. from the URL) and every search match are revealed, even when folded.
  const selectedAncestors = useMemo(
    () => ancestorIds(shown, new Set(selectedSpanId ? [selectedSpanId] : [])),
    [shown, selectedSpanId],
  );
  const matchAncestors = useMemo(() => ancestorIds(shown, matches), [shown, matches]);
  const effectiveCollapsed = new Set(
    [...collapsed].filter((id) => !selectedAncestors.has(id) && !matchAncestors.has(id)),
  );
  const rows = flattenSpanTree(shown, effectiveCollapsed);
  // Scrolling a virtualized tree re-renders it with the same tree; don't re-walk 1,000 spans.
  const meta = useMemo(() => treeItemMeta(shown), [shown]);
  const nodes = useMemo(() => indexNodes(tree), [tree]);
  const parents = useMemo(() => parentSpanIds(tree), [tree]);
  const items = waterfallItems(rows);
  // The Doctor's span (D10), or its nearest shown ancestor when it's folded away (a retry group,
  // a collapsed subtree) or filtered out, so the flag never vanishes.
  const doctor = useMemo(() => {
    const id = doctorSpanId(spans);
    return id === null ? null : { id, above: ancestorIds(tree, new Set([id])) };
  }, [spans, tree]);
  const doctorRowId = rows.some((row) => row.span.span_id === doctor?.id)
    ? (doctor?.id ?? null)
    : (rows.findLast((row) => doctor?.above.has(row.span.span_id))?.span.span_id ?? null);

  function setExpanded(spanId: string, expanded: boolean) {
    const next = new Set(effectiveCollapsed);
    if (expanded) {
      next.delete(spanId);
    } else {
      next.add(spanId);
      // Collapsing an ancestor of the selection moves the selection up to it.
      if (selectedAncestors.has(spanId)) {
        onSelect(spanId);
      }
    }
    setCollapsed(next);
  }

  return {
    rows,
    items,
    topLevel: topLevelPositions(items),
    doctorRowId,
    meta,
    nodes,
    parents,
    query,
    setQuery,
    matches,
    kind,
    setKind,
    collapseRetries: hasRetries ? collapseRetries : null,
    setCollapseRetries,
    setExpanded,
    expandAll: () => {
      setCollapsed(new Set());
    },
    collapseAll: () => {
      setCollapsed(new Set(parents));
    },
  };
}
