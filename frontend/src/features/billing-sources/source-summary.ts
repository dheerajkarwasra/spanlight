import {
  aggregateSources,
  BILLING_REASONS,
  type SourceAggregate,
  type useMappedSourceReports,
} from "@/features/attribution/cost-strip";

/**
 * Pure logic of the Reconciliation tab's sources table (spec §3.5): each mapped source's figures
 * over the report window, and why an unknown one is unknown. Never 0 for an unknown figure.
 */

export type SourceItem = ReturnType<typeof useMappedSourceReports>["items"][number];

/** The report window's first and last UTC day; null when the range starts after today. */
export type Days = { first: string; last: string } | null;

export type Figures =
  | { state: "pending" }
  | { state: "unknown"; reason: string }
  | { state: "ready"; aggregate: SourceAggregate };

export type Row = SourceItem & { figures: Figures };

export function figuresOf({ source, report }: SourceItem, days: Days): Figures {
  if (days === null) {
    return { state: "unknown", reason: BILLING_REASONS.noWindow };
  }
  if (report.data) {
    const aggregate = aggregateSources([{ name: source.name, report: report.data }]);
    return { state: "ready", aggregate };
  }
  return report.isError
    ? { state: "unknown", reason: BILLING_REASONS.failed }
    : { state: "pending" };
}

export type Reason = (aggregate: SourceAggregate) => string;

/**
 * Why Spanlight's sum over the billed days is unknown: no billed day, its rollups still being
 * built (the API then leaves the residual null too), or no priced call on those days.
 */
const spanlightReason: Reason = (a) => {
  if (a.providerUsd === null) {
    return BILLING_REASONS.noProvider;
  }
  return a.residualUsd === null ? BILLING_REASONS.rollupPending : BILLING_REASONS.noSpanlightCost;
};

/** Why a money figure of a source is unknown once its report has loaded. */
export const SPANLIGHT_REASONS: Record<"providerUsd" | "spanlightUsd", Reason> = {
  providerUsd: () => BILLING_REASONS.noProvider,
  spanlightUsd: spanlightReason,
};
