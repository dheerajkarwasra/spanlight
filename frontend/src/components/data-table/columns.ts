import type { ReactNode } from "react";

export type SortOrder = "asc" | "desc";

export interface SortState {
  key: string;
  order: SortOrder;
}

export interface DataColumn<Row> {
  id: string;
  header: string;
  /** Shown in the header cell instead of `header` (e.g. a select-all checkbox); not sortable. */
  headerCell?: ReactNode;
  cell: (row: Row) => ReactNode;
  /** One `grid-template-columns` track, e.g. "88px" or "minmax(160px,1fr)". */
  width: string;
  /** Right-aligned with tabular numbers. */
  align?: "end";
  /** Listed in Table settings so the viewer can hide or show it. */
  hideable?: boolean;
  defaultHidden?: boolean;
  /** Shown by default only when the table box is at least this wide (px). */
  minContainer?: number;
  /** Makes the header a sort button; the value sent to `sort.onChange`. */
  sortKey?: string;
  /**
   * Stays in view while scrolling sideways. Mark the identity column and every column before it
   * (e.g. the select column), so nothing scrolls into the gap on its left.
   */
  sticky?: boolean;
}

/**
 * The columns to render: a stored choice wins; otherwise a column shows unless it is hidden by
 * default or the container is narrower than its `minContainer`.
 */
export function visibleColumns<Row>(
  columns: readonly DataColumn<Row>[],
  overrides: Record<string, boolean>,
  containerWidth: number,
): DataColumn<Row>[] {
  return columns.filter(
    (col) =>
      overrides[col.id] ??
      (!col.defaultHidden &&
        // 0 means not measured yet (and always in jsdom): show the default columns.
        (containerWidth === 0 || containerWidth >= (col.minContainer ?? 0))),
  );
}

export function gridTemplate<Row>(columns: readonly DataColumn<Row>[]): string {
  return columns.map((col) => col.width).join(" ");
}

/** A new sort key starts descending; the same key flips between descending and ascending. */
export function nextSort(current: SortState | undefined, key: string): SortState {
  if (current?.key !== key) {
    return { key, order: "desc" };
  }
  return { key, order: current.order === "desc" ? "asc" : "desc" };
}
