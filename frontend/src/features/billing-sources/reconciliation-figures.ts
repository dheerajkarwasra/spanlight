import { varianceTone, type VarianceTone } from "@/features/traces";

import { formatSignedUsd, formatUsd, formatVariance } from "./reconciliation-format";

/**
 * The figures of the reconciliation page: money, or "—" with the reason it is unknown, and the
 * variance with its band. The server sends an unknown figure as null, never as 0, and the page
 * keeps it that way.
 */

export const REASONS = {
  notFetched: "Not fetched yet: no successful fetch covers this day.",
  rollupPending:
    "Rollup pending: Spanlight’s figures for this day are still being built. Reload in a minute.",
  notRetained:
    "Incomplete: spans no longer retained for this day, so Spanlight’s figure is partial.",
  notPriced: "Counted, not priced: Spanlight knows the calls or tokens but has no USD figure.",
  noPrice: "No price: none of these calls has a price, so Spanlight has no USD figure.",
  noCalls: "No calls: Spanlight recorded no call for this on this day.",
  noProviderAmount:
    "No variance: the provider billed nothing here, so there is nothing to compare against.",
} as const;

/** A figure as text, or null with the reason it is unknown. */
export interface Figure {
  text: string | null;
  reason: string | null;
}

export interface VarianceFigure extends Figure {
  tone: VarianceTone;
  /** "within 2 %", "2–10 %", "over 10 %"; null when unknown. */
  band: string | null;
  /** "Variance +3.1 percent, 2 to 10 percent band"; null when unknown. */
  accessibleName: string | null;
}

/** Money as text ("$12.35", or "+$0.42" when `signed`), or unknown with `reason`. */
export function money(value: string | null, reason: string, signed = false): Figure {
  if (value === null) {
    return { text: null, reason };
  }
  return { text: signed ? formatSignedUsd(value) : formatUsd(value), reason: null };
}

const BANDS: Record<Exclude<VarianceTone, "unknown">, { band: string; spoken: string }> = {
  within2: { band: "within 2 %", spoken: "within 2 percent" },
  within10: { band: "2–10 %", spoken: "2 to 10 percent band" },
  over: { band: "over 10 %", spoken: "over 10 percent" },
};

/** A ratio's text, band and spoken name; unknown with `reason` when the ratio is null. */
export function varianceFigure(ratio: string | null, reason: string): VarianceFigure {
  const tone = varianceTone(ratio === null ? null : Number(ratio));
  if (ratio === null || tone === "unknown") {
    return { text: null, reason, tone: "unknown", band: null, accessibleName: null };
  }
  const text = formatVariance(ratio);
  const { band, spoken } = BANDS[tone];
  const words = text.replace(" %", " percent").replace("−", "minus ");
  return { text, reason: null, tone, band, accessibleName: `Variance ${words}, ${spoken}` };
}
