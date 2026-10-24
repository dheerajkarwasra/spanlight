import type { SpanRow } from "./span-tree";

/** What the waterfall renders: span rows, and ghost rows for parents that never arrived. */
export type WaterfallItem =
  { type: "span"; row: SpanRow } | { type: "ghost"; parentSpanId: string; depth: number };

/** The rows with one ghost row above the first orphan of each missing parent (spec D11). */
export function waterfallItems(rows: readonly SpanRow[]): WaterfallItem[] {
  const ghosted = new Set<string>();
  return rows.flatMap((row): WaterfallItem[] => {
    const parentSpanId = row.span.parent_span_id;
    const item: WaterfallItem = { type: "span", row };
    if (!row.orphaned || parentSpanId === null || ghosted.has(parentSpanId)) {
      return [item];
    }
    ghosted.add(parentSpanId);
    return [{ type: "ghost", parentSpanId, depth: row.depth }, item];
  });
}

/**
 * 1-based positions among the top-level items, ghost rows counted, so `aria-posinset` and
 * `aria-setsize` agree across the roots and the ghosts beside them.
 */
export function topLevelPositions(items: readonly WaterfallItem[]): Map<WaterfallItem, number> {
  const top = items.filter((item) => (item.type === "ghost" ? item.depth : item.row.depth) === 0);
  return new Map(top.map((item, index) => [item, index + 1]));
}

/**
 * Name column | timeline | duration (Figma "Traces/Span row": 244 / 262 / 60 at 582px). Shared
 * by the time axis and every row so the ticks line up with the bars. In a tree pane narrower than
 * 420 px (the 1024 two-pane layout, the peek, phones) the timeline column hides, so the name gets
 * its width, and each row draws its bar under the name instead (`TIMELINE_CELL`, `INLINE_BAR`).
 */
export const ROW_GRID =
  "grid grid-cols-[minmax(0,1fr)_3.25rem] items-center gap-2 @min-[420px]:grid-cols-[minmax(0,42%)_minmax(0,1fr)_3.75rem]";
/** The timeline cell of `ROW_GRID`: shown from a 420 px tree pane. */
export const TIMELINE_CELL = "hidden @min-[420px]:block";
/** The thin bar under the name that stands in for the timeline cell in a narrow tree pane. */
export const INLINE_BAR = "@min-[420px]:hidden";
/** Figma: name cell padding-left = 8px + 18px per depth level. */
export function indentPx(depth: number): number {
  return 8 + 18 * depth;
}

export function spanRowId(spanId: string): string {
  return `span-row-${spanId}`;
}
