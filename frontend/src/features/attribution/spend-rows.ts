import type { AttributionBucket, AttributionTotals } from "@/lib/api";

import type { AttributionView } from "./attribution-grid";
import { shareOf } from "./footer-format";

/** One table row: a value, the traces without the dimension, the folded values, or the total. */
export type SpendRow = {
  key: string;
  figures: AttributionTotals;
  /** The share of cost; `"total"` on the total row, which is the whole. */
  share: number | null | "total";
} & (
  | { kind: "value"; value: string }
  | { kind: "none" }
  | { kind: "other"; bucket: AttributionBucket }
  | { kind: "total" }
);

/** The loaded values, then none, other and the total (keys can't clash with a value's `v:`). */
export function spendRows(view: AttributionView): SpendRow[] {
  const { none, other, totals } = view.page;
  const rows: SpendRow[] = view.items.map((row) => ({
    key: `v:${row.value}`,
    kind: "value",
    value: row.value,
    figures: row,
    share: row.share_of_cost,
  }));
  if (none) rows.push({ key: "none", kind: "none", figures: none, share: shareOf(none, view) });
  if (other) {
    rows.push({
      key: "other",
      kind: "other",
      bucket: other,
      figures: other,
      share: shareOf(other, view),
    });
  }
  rows.push({ key: "total", kind: "total", figures: totals, share: "total" });
  return rows;
}
