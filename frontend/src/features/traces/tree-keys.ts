import type { SpanRow } from "./span-tree";

/** What a key does on the focused row: move focus, fold or unfold; null when it does nothing. */
export type TreeKeyAction = { focus: string } | { toggle: string; expanded: boolean } | null;

/**
 * The ARIA tree keyboard model over the shown rows (ghost rows aren't rows, so arrows skip
 * them). `undefined` for a key the tree doesn't handle.
 */
export function treeKeyAction(
  rows: readonly SpanRow[],
  index: number,
  key: string,
): TreeKeyAction | undefined {
  const row = rows[index];
  if (!row) {
    return undefined;
  }
  const focus = (target: SpanRow | undefined) => (target ? { focus: target.span.span_id } : null);
  switch (key) {
    case "ArrowDown":
      return focus(rows[index + 1]);
    case "ArrowUp":
      return focus(rows[index - 1]);
    case "Home":
      return focus(rows[0]);
    case "End":
      return focus(rows.at(-1));
    case "ArrowRight":
      if (!row.hasChildren) {
        return null;
      }
      return row.expanded ? focus(rows[index + 1]) : { toggle: row.span.span_id, expanded: true };
    case "ArrowLeft":
      if (row.hasChildren && row.expanded) {
        return { toggle: row.span.span_id, expanded: false };
      }
      // The shown parent: the nearest row above, one level up (a retry group for its attempts).
      return focus(rows.slice(0, index).findLast((candidate) => candidate.depth === row.depth - 1));
    default:
      return undefined;
  }
}
