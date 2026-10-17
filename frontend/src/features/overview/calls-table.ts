import type { ChartDataTable } from "@/components/chart-data-table";
import type { Bucket } from "@/lib/api";
import { formatInteger, formatPercent } from "@/lib/format";
import type { Band } from "@/lib/incident-bands";

import { bucketNoun, formatBucketLabel, type ChartPoint } from "./chart-data";
import { incidentsAt } from "./incident-overlay";

/** What a bucket's incidents cell says when no API-affected incident overlapped it. */
export const NO_INCIDENT = "none";

/**
 * The calls/errors numbers as a table, one row per bucket: the visible "Show table" view and the
 * chart's screen-reader twin. `bands` adds the "Provider incidents" column; null leaves it out
 * (incidents unknown, or switched off by the viewer).
 */
export function callsTable(
  points: readonly ChartPoint[],
  bucket: Bucket,
  bands: readonly Band[] | null,
): ChartDataTable {
  const noun = bucketNoun(bucket);
  const columns = [noun === "hour" ? "Hour" : "Day", "LLM calls", "Errors", "Error rate"];
  return {
    caption: `LLM calls and errors per ${noun}`,
    columns: bands === null ? columns : [...columns, "Provider incidents"],
    rows: points.map((point) => {
      const rate = point.llmCalls > 0 ? point.errors / point.llmCalls : null;
      const cells = [
        formatBucketLabel(point.bucketStart, bucket),
        formatInteger(point.llmCalls) ?? "0",
        formatInteger(point.errors) ?? "0",
        formatPercent(rate) ?? "—",
      ];
      if (bands !== null) {
        const titles = incidentsAt(bands, point.bucketStart, bucket);
        cells.push(titles.length === 0 ? NO_INCIDENT : titles.join("; "));
      }
      return { key: point.bucketStart, cells };
    }),
  };
}
