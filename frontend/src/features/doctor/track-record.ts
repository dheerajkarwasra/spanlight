import type { PrecisionKind } from "@/lib/api";

import { formatPrecisionRow, PRECISION_TARGET } from "./precision-format";

/**
 * The finding's track record line (M2), from its check's precision row: the record once there
 * are enough votes to mean something, otherwise how many there are.
 */
export function trackRecordText(row: PrecisionKind | undefined): string {
  if (row === undefined || row.n === 0) {
    return "No votes on this check yet";
  }
  if (row.n < PRECISION_TARGET.minAnswers) {
    return `Not enough votes yet (${String(row.n)} of ${String(PRECISION_TARGET.minAnswers)})`;
  }
  const view = formatPrecisionRow(row);
  const detail = [view.precision === null ? null : `precision ${view.precision}`, view.interval]
    .filter((part) => part !== null)
    .join(", ");
  const record = `This check was right ${String(row.right)} of ${String(row.n)} times`;
  return detail === "" ? record : `${record} (${detail})`;
}
