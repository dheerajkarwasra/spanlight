import type { AttributionBucket, AttributionSort } from "@/lib/api";
import { formatInteger } from "@/lib/format";

import type { AttributionView } from "./attribution-grid";
import { sortPhrase } from "./attribution-sort";
import type { DrillContext } from "./drill-down";

const NONE_NAMES = { trace_name: "No trace name", user: "No user", release: "No release" };

/** "No customer_id", "No trace name": the traces without the dimension. */
export function noneText(drill: DrillContext): string {
  return drill.groupBy === "metadata" ? `No ${drill.key ?? "key"}` : NONE_NAMES[drill.groupBy];
}

interface SentenceInput {
  loaded: number;
  sort: AttributionSort;
  hasMore: boolean;
  none: string | null;
  hasOther: boolean;
}

/** "Top 8 values by cost. The total covers every value, including those not loaded yet, plus …" */
export function footerSentence({ loaded, sort, hasMore, none, hasOther }: SentenceInput): string {
  const noun = loaded === 1 ? "value" : "values";
  const extras = [none === null ? null : `“${none}”`, hasOther ? "the folded values" : null];
  const plus = extras.filter((part) => part !== null).join(" and ");
  return [
    `Top ${formatInteger(loaded)} ${noun} ${sortPhrase(sort)}.`,
    ` The total covers every value${hasMore ? ", including those not loaded yet" : ""}`,
    plus ? `, plus ${plus}.` : ".",
  ].join("");
}

/** A bucket's share of the window's cost; the API rounds row shares, buckets get the same rule. */
export function shareOf(bucket: AttributionBucket, view: AttributionView): number | null {
  const cost = bucket.cost_usd === null ? null : Number(bucket.cost_usd);
  const total = view.page.totals.cost_usd === null ? null : Number(view.page.totals.cost_usd);
  if (cost === null || total === null || total === 0) {
    return null;
  }
  return Math.round((cost / total) * 10_000) / 10_000;
}
