import { errorMessage, isApiError, type PrecisionKind } from "@/lib/api";

/*
 * The Doctor's Precision tab: how often each check was right, from members' "Was this right?"
 * answers. The server computes the ratios and whether a kind meets the target; this file only
 * words and places them.
 */

/** Spec §16.2: precision ≥ 0.8 with a Wilson 95 % lower bound ≥ 0.6, on at least 20 answers. */
export const PRECISION_TARGET = { precision: 0.8, lowerBound: 0.6, minAnswers: 20 } as const;

export function targetLine(): string {
  const { precision, lowerBound, minAnswers } = PRECISION_TARGET;
  return `Target: precision ≥ ${String(precision)}, lower bound ≥ ${String(lowerBound)}, at least ${String(minAnswers)} answers.`;
}

/** `meets` and `below` only from 20 answers on; fewer are never marked. */
export type PrecisionStatus = "meets" | "below" | "insufficient" | "none";

export interface PrecisionRowView {
  /** Two places, e.g. "0.92"; null when there are no answers (shown as "—"). */
  precision: string | null;
  /** The Wilson 95 % interval, e.g. "0.70–0.97"; null when unknown. */
  interval: string | null;
  /** "not enough feedback yet" under 20 answers, "no answers yet" at none. */
  note: string | null;
  status: PrecisionStatus;
  /** The interval and the point on a 0–100 scale, for the strip; null when unknown. */
  bar: { low: number; high: number; point: number } | null;
}

function ratio(value: string | null): number | null {
  if (value === null) {
    return null;
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function twoPlaces(value: number): string {
  return value.toFixed(2);
}

function percentOf(value: number): number {
  return Math.round(value * 10_000) / 100;
}

function status(row: PrecisionKind): PrecisionStatus {
  if (row.n === 0) {
    return "none";
  }
  if (row.n < PRECISION_TARGET.minAnswers) {
    return "insufficient";
  }
  return row.meets_target ? "meets" : "below";
}

const NOTES: Record<PrecisionStatus, string | null> = {
  meets: null,
  below: null,
  insufficient: "not enough feedback yet",
  none: "no answers yet",
};

export function formatPrecisionRow(row: PrecisionKind): PrecisionRowView {
  const point = ratio(row.precision);
  const low = ratio(row.wilson_low);
  const high = ratio(row.wilson_high);
  const known = low !== null && high !== null;
  const rowStatus = status(row);
  return {
    precision: point === null ? null : twoPlaces(point),
    interval: known ? `${twoPlaces(low)}–${twoPlaces(high)}` : null,
    note: NOTES[rowStatus],
    status: rowStatus,
    bar:
      known && point !== null
        ? { low: percentOf(low), high: percentOf(high), point: percentOf(point) }
        : null,
  };
}

/** Most answers first, then by name: the kinds whose number means something lead. */
export function sortPrecisionRows(rows: readonly PrecisionKind[]): PrecisionKind[] {
  return [...rows].sort((a, b) => b.n - a.n || a.label.localeCompare(b.label));
}

/** Whether anyone has answered for any kind; without answers the tab shows its empty state. */
export function hasAnswers(rows: readonly PrecisionKind[]): boolean {
  return rows.some((row) => row.n > 0);
}

/** "Wilson ranges: Retry storm 0.75–0.98 · …" for the kinds with enough answers. */
export function intervalFootnote(rows: readonly PrecisionKind[]): string | null {
  const ranges = rows.flatMap((row) => {
    const view = formatPrecisionRow(row);
    const marked = view.status === "meets" || view.status === "below";
    return marked && view.interval !== null ? [`${row.label} ${view.interval}`] : [];
  });
  return ranges.length === 0 ? null : `Wilson ranges: ${ranges.join(" · ")}.`;
}

/** The error card's line: "The server answered 503. The insight tabs still work." */
export function precisionErrorText(error: unknown): string {
  const cause = isApiError(error)
    ? `The server answered ${String(error.status)}.`
    : errorMessage(error);
  return `${cause} The insight tabs still work.`;
}
