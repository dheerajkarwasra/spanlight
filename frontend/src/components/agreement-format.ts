/** Words for judge–reviewer agreement (spec UX Phase 6 §3), shared by the card and the badge. */
import type { AgreementSummary } from "@/lib/api";
import { formatInteger } from "@/lib/format";

/** Pairs needed before κ is shown; the summary does not carry the server's `min_pairs`. */
export const MIN_PAIRS = 30;

export type AgreementBand = "Strong agreement" | "Moderate agreement" | "Weak agreement";

/** ≥ 0.80 strong, 0.60–0.79 moderate, below 0.60 weak. */
export function bandText(kappa: number): AgreementBand {
  if (kappa >= 0.8) {
    return "Strong agreement";
  }
  return kappa >= 0.6 ? "Moderate agreement" : "Weak agreement";
}

/**
 * "0.72"; "—" when κ cannot be computed. Truncated toward zero, not rounded, so the shown value
 * stays in κ's band: 0.5996 (weak) is "0.59", never "0.60". Snapped to the server's 4 places
 * first, so float error cannot turn 0.29 into "0.28".
 */
export function formatKappa(kappa: number | null): string {
  return kappa === null ? "—" : (Math.trunc(Math.round(kappa * 10_000) / 100) / 100).toFixed(2);
}

/** "κ 0.72 · 48 pairs", "κ 0.41 · weak", "Not calibrated · 12/30"; null for an unsupported score. */
export function badgeText(summary: AgreementSummary): string | null {
  const pairs = formatInteger(summary.pairs) ?? "";
  switch (summary.status) {
    case "ok":
      return summary.flagged
        ? `κ ${formatKappa(summary.kappa)} · weak`
        : `κ ${formatKappa(summary.kappa)} · ${pairs} pairs`;
    case "not_enough_pairs":
      return `Not calibrated · ${pairs}/${String(MIN_PAIRS)}`;
    case "undefined":
      return `κ — · ${pairs} pairs`;
    case "unsupported":
      return null;
  }
}

/** The badge's accessible name: the whole sentence, so the tooltip adds nothing required. */
export function badgeLabel(summary: AgreementSummary): string {
  const pairs = formatInteger(summary.pairs) ?? "";
  const lead = "Judge agreement with reviewers:";
  switch (summary.status) {
    case "not_enough_pairs":
      return `${lead} not calibrated, ${pairs} of ${String(MIN_PAIRS)} paired traces`;
    case "undefined":
      return `${lead} kappa can't be computed over ${pairs} traces`;
    default:
      return `${lead} kappa ${formatKappa(summary.kappa)} over ${pairs} traces${summary.flagged ? ", weak" : ""}`;
  }
}
