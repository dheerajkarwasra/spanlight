import { CircleAlert, CircleCheck, CircleX } from "lucide-react";

import { UNKNOWN_TOTAL_REASON } from "@/components/cost-value";
import {
  formatUsd,
  formatVariance,
  sumMoney,
} from "@/features/billing-sources/reconciliation-format";
import { shortDay } from "@/features/billing-sources/window-picker";
import { varianceTone, type VarianceTone } from "@/features/traces";
import type {
  AttributionResponse,
  ReconciliationDay,
  ReconciliationReport,
  TokenMix,
} from "@/lib/api";
import { formatClock } from "@/lib/clock-format";
import { formatInteger, parseMoney, pluralize } from "@/lib/format";
import type { ResolvedRange } from "@/lib/time-range";

/**
 * The cost strip's figures (spec §3.2, rulings 1–5): telemetry spend from the attribution totals,
 * the provider's bill and the variance from the billing sources that map this project, and the
 * unpriced calls. Unknown figures stay null with a reason, never 0.
 */

const DAY_MS = 86_400_000;
const MAX_REPORT_DAYS = 31;

function dayOf(ms: number): string {
  return new Date(ms).toISOString().slice(0, 10);
}

/**
 * The global range snapped to whole UTC days (inclusive), ending today at the latest and holding
 * at most 31 days (`capped` when cut); null when it starts after today.
 */
export function reportWindow(
  range: ResolvedRange,
  today: string,
): { first: string; last: string; capped: boolean } | null {
  const first = dayOf(Date.parse(range.from));
  const rangeLast = dayOf(Date.parse(range.to) - 1);
  const last = rangeLast < today ? rangeLast : today;
  if (first > last) {
    return null;
  }
  const earliest = dayOf(Date.parse(`${last}T00:00:00Z`) - (MAX_REPORT_DAYS - 1) * DAY_MS);
  return first < earliest
    ? { first: earliest, last, capped: true }
    : { first, last, capped: false };
}

export interface SourceAggregate {
  providerUsd: string | null;
  spanlightUsd: string | null;
  explainedUsd: string | null;
  residualUsd: string | null;
  /**
   * `residual / provider`, the API's `variance_ratio` (`reconciliation/arithmetic.py`), so the
   * strip matches the report page; null when nothing was billed.
   */
  variance: number | null;
  /** The earliest of the sources' last settled days, so "through" holds for every source. */
  lastSettledDay: string | null;
  provisionalDays: number;
  /** The most projects any one source covers: its bill is for all of them. */
  projectCount: number;
  names: string[];
}

function sumKnown(days: ReconciliationDay[], pick: (day: ReconciliationDay) => string | null) {
  const values = days.map(pick).filter((value) => value !== null);
  return values.length > 0 ? sumMoney(values) : null;
}

/** Sums the sources over the days the provider has billed (non-null `provider_usd`) only. */
export function aggregateSources(
  items: { name: string; report: ReconciliationReport }[],
): SourceAggregate {
  const billedBySource = items.map(({ report }) =>
    report.days.filter((day) => day.provider_usd !== null),
  );
  const billed = billedBySource.flat();
  const providerUsd = sumKnown(billed, (day) => day.provider_usd);
  const spanlightUsd = sumKnown(billed, (day) => day.spanlight_usd);
  const residualUsd = sumKnown(billed, (day) => day.residual_usd);
  const provider = parseMoney(providerUsd);
  const residual = parseMoney(residualUsd);
  const settled = billedBySource
    .map((days) => days.filter((day) => !day.provisional).map((day) => day.day))
    .filter((days) => days.length > 0)
    .map((days) => days.sort().at(-1) ?? "")
    .sort();
  return {
    providerUsd,
    spanlightUsd,
    explainedUsd: sumKnown(billed, (day) => day.explained_usd),
    residualUsd,
    variance: provider !== null && provider > 0 && residual !== null ? residual / provider : null,
    lastSettledDay: settled[0] ?? null,
    provisionalDays: new Set(billed.filter((day) => day.provisional).map((day) => day.day)).size,
    projectCount: Math.max(0, ...items.map(({ report }) => report.projects.length)),
    names: items.map(({ name }) => name),
  };
}

export type TokenClass = "input" | "output" | "cache_read" | "cache_write";

export interface TokenShare {
  key: TokenClass;
  tokens: number;
  /** Whole percent; the four add up to 100 (largest remainder). */
  pct: number;
}

export const TOKEN_CLASS_LABELS: Record<TokenClass, string> = {
  input: "Input",
  output: "Output",
  cache_read: "Cache read",
  cache_write: "Cache write",
};

/** The four classes' shares of all tokens; null when a class is unknown or none was counted. */
export function tokenShares(mix: TokenMix): TokenShare[] | null {
  const counts: [TokenClass, number | null][] = [
    ["input", mix.uncached_input_tokens],
    ["output", mix.output_tokens],
    ["cache_read", mix.cache_read_tokens],
    ["cache_write", mix.cache_write_tokens],
  ];
  const known = counts.flatMap(([key, tokens]) => (tokens === null ? [] : [{ key, tokens }]));
  const total = known.reduce((sum, { tokens }) => sum + tokens, 0);
  if (known.length < counts.length || total === 0) {
    return null;
  }
  const exact = known.map(({ key, tokens }) => ({ key, tokens, raw: (tokens * 100) / total }));
  const shares = exact.map(({ key, tokens, raw }) => ({ key, tokens, pct: Math.floor(raw) }));
  const left = 100 - shares.reduce((sum, share) => sum + share.pct, 0);
  const byRemainder = exact
    .map(({ raw }, index) => ({ index, rest: raw - Math.floor(raw) }))
    .sort((a, b) => b.rest - a.rest);
  for (const { index } of byRemainder.slice(0, left)) {
    const share = shares[index];
    if (share) {
      share.pct += 1;
    }
  }
  return shares;
}

/** "61 %", or "<1 %" for a class with tokens that rounds to nothing. */
export function shareText(share: TokenShare): string {
  return share.pct === 0 && share.tokens > 0 ? "<1 %" : `${String(share.pct)} %`;
}

/** The bar's accessible name: every share in words. */
export function mixLabel(shares: TokenShare[]): string {
  const parts = shares.map(
    (share) => `${TOKEN_CLASS_LABELS[share.key].toLowerCase()} ${shareText(share)}`,
  );
  return `Token mix: ${parts.join(", ")}`;
}

/** "All environments · Oct 14–20 UTC": the rollup has no environment and whole UTC days. */
export function mixCaption(mix: TokenMix): string {
  const first = shortDay(mix.first_day);
  const last = shortDay(mix.last_day);
  let days = `${first} – ${last}`;
  if (mix.first_day === mix.last_day) {
    days = first;
  } else if (mix.first_day.slice(0, 7) === mix.last_day.slice(0, 7)) {
    days = `${first}–${mix.last_day.slice(8).replace(/^0/, "")}`;
  }
  return `All environments · ${days} UTC`;
}

export type Billing =
  | { state: "pending" }
  | { state: "forbidden" }
  | { state: "failed" }
  | { state: "not_connected" }
  | { state: "no_window" }
  | { state: "ready"; aggregate: SourceAggregate; capped: boolean };

/** Where a card leads; the component maps these to routes. */
export type CardLink = "spend" | "reconciliation" | "pricing" | "billing-sources";

export interface StripCard {
  id: "spend" | "billed" | "variance" | "unpriced";
  label: string;
  pending: boolean;
  value: string | null;
  unknownReason?: string;
  /** Spend with unpriced calls: shown "≥ $x". */
  lowerBound?: boolean;
  /** The first line stays on phones; the rest show from 768 px. */
  lines: string[];
  /** Null when the card has nowhere useful to go (a failure, a hidden organization). */
  href: CardLink | null;
  tone?: VarianceTone;
  /** Read after the value in the link's name, e.g. "within 10 %". */
  spoken?: string;
  freshness?: string;
  retry: boolean;
}

export const BILLING_REASONS = {
  forbidden: "You can't see this organization's billing sources.",
  failed: "Couldn't load billing data",
  notConnected: "No billing source maps this project.",
  noWindow: "The window starts after today (UTC).",
  noProvider: "The provider has billed nothing for these days yet.",
  rollupPending: "Spanlight's figures for these days are still being built.",
  noSpanlightCost: "Spanlight recorded no priced call on these days.",
} as const;

/**
 * Why a source's variance is unknown: nothing billed to compare against, or (the provider billed
 * but no residual) Spanlight's rollups for those days are still being built, the only case the
 * API leaves a billed day's residual null.
 */
export function varianceReason(aggregate: SourceAggregate): string {
  const provider = parseMoney(aggregate.providerUsd);
  return provider !== null && provider > 0 && aggregate.residualUsd === null
    ? BILLING_REASONS.rollupPending
    : BILLING_REASONS.noProvider;
}

/** The band's icon and its colour; the words (`TONE_LABELS`) sit beside it in text tokens. */
export const TONE_ICONS: Record<
  Exclude<VarianceTone, "unknown">,
  { icon: typeof CircleCheck; color: string }
> = {
  within2: { icon: CircleCheck, color: "text-success" },
  within10: { icon: CircleAlert, color: "text-warning" },
  over: { icon: CircleX, color: "text-danger" },
};

export const TONE_LABELS: Record<Exclude<VarianceTone, "unknown">, string> = {
  within2: "Within 2 %",
  within10: "Within 10 %",
  over: "Over 10 %",
};

const SPEND_FAILED = "Couldn't load spend";

interface StripInput {
  attribution: AttributionResponse | undefined;
  attributionFailed: boolean;
  billing: Billing;
}

function freshness(page: AttributionResponse): string {
  if (page.source === "raw") {
    return "Live";
  }
  const at = page.computed_at === null ? null : formatClock(page.computed_at);
  return at === null ? "Not computed yet" : `Updated ${at}`;
}

function spendCards(input: StripInput): [StripCard, StripCard] {
  const page = input.attribution;
  const base = { pending: page === undefined && !input.attributionFailed, lines: [], retry: false };
  if (page === undefined) {
    const failed = input.attributionFailed
      ? { unknownReason: SPEND_FAILED, lines: [SPEND_FAILED], retry: true, href: null }
      : { href: null };
    return [
      { ...base, ...failed, id: "spend", label: "Telemetry spend", value: null },
      { ...base, ...failed, id: "unpriced", label: "Unpriced calls", value: null },
    ];
  }
  const { cost_usd: cost, unpriced_calls: unpriced, llm_calls: calls } = page.totals;
  return [
    {
      ...base,
      id: "spend",
      label: "Telemetry spend",
      value: cost === null ? null : formatUsd(cost),
      unknownReason: calls === 0 ? "No LLM calls in this window." : UNKNOWN_TOTAL_REASON,
      lowerBound: unpriced > 0,
      href: "spend",
      freshness: freshness(page),
    },
    {
      ...base,
      id: "unpriced",
      label: "Unpriced calls",
      value: formatInteger(unpriced),
      lines: [unpriced > 0 ? "Spend above is a lower bound" : "Every call has a price"],
      href: "pricing",
    },
  ];
}

function billedLines(aggregate: SourceAggregate, capped: boolean): string[] {
  const through = aggregate.lastSettledDay
    ? ` · through ${shortDay(aggregate.lastSettledDay)}`
    : "";
  const lines = [`${aggregate.names.join(", ")}${through}`];
  if (aggregate.provisionalDays > 0) {
    lines.push(`${pluralize(aggregate.provisionalDays, "day")} provisional`);
  }
  if (aggregate.projectCount > 1) {
    lines.push(`Across ${String(aggregate.projectCount)} projects`);
  }
  if (capped) {
    lines.push(`Last ${String(MAX_REPORT_DAYS)} days`);
  }
  return lines;
}

function billingCards(billing: Billing): [StripCard, StripCard] {
  const billed = { id: "billed", label: "Provider billed", pending: false, retry: false } as const;
  const variance = { id: "variance", label: "Variance", pending: false, retry: false } as const;
  const unknown = (reason: string, href: CardLink | null, lines: string[] = [], retry = false) =>
    ({ value: null, unknownReason: reason, href, lines, retry }) as const;

  switch (billing.state) {
    case "pending":
      return [
        { ...billed, ...unknown("", null), pending: true },
        { ...variance, ...unknown("", null), pending: true },
      ];
    case "forbidden":
    case "failed": {
      const reason = BILLING_REASONS[billing.state];
      const retry = billing.state === "failed";
      return [
        { ...billed, ...unknown(reason, null, [reason], retry) },
        { ...variance, ...unknown(reason, null, [reason], retry) },
      ];
    }
    case "not_connected":
      return [
        {
          ...billed,
          value: "Not connected",
          lines: ["Connect a billing source"],
          href: "billing-sources",
        },
        { ...variance, ...unknown(BILLING_REASONS.notConnected, "billing-sources") },
      ];
    case "no_window":
      return [
        { ...billed, ...unknown(BILLING_REASONS.noWindow, "reconciliation") },
        { ...variance, ...unknown(BILLING_REASONS.noWindow, "reconciliation") },
      ];
    case "ready":
      return readyCards(billing.aggregate, billing.capped, billed, variance);
  }
}

function readyCards(
  aggregate: SourceAggregate,
  capped: boolean,
  billed: Pick<StripCard, "id" | "label" | "pending" | "retry">,
  variance: Pick<StripCard, "id" | "label" | "pending" | "retry">,
): [StripCard, StripCard] {
  const lines = billedLines(aggregate, capped);
  const billedCard: StripCard = {
    ...billed,
    value: aggregate.providerUsd === null ? null : formatUsd(aggregate.providerUsd),
    unknownReason: BILLING_REASONS.noProvider,
    lines,
    href: "reconciliation",
  };
  const tone = varianceTone(aggregate.variance);
  if (aggregate.variance === null || tone === "unknown") {
    const reason = varianceReason(aggregate);
    return [
      billedCard,
      { ...variance, value: null, unknownReason: reason, lines: [], href: "reconciliation" },
    ];
  }
  const label = TONE_LABELS[tone];
  const { explainedUsd: explained, residualUsd: residual } = aggregate;
  const split =
    explained !== null && residual !== null
      ? [`Explained ${formatUsd(explained)} · unexplained ${formatUsd(residual)}`]
      : [];
  return [
    billedCard,
    {
      ...variance,
      value: formatVariance(aggregate.variance.toFixed(6)),
      tone,
      spoken: label.toLowerCase(),
      lines: [label, ...split],
      href: "reconciliation",
    },
  ];
}

/** The four cards in order: telemetry spend, provider billed, variance, unpriced calls. */
export function costStripCards(input: StripInput): StripCard[] {
  const [spend, unpriced] = spendCards(input);
  const [billed, variance] = billingCards(input.billing);
  return [spend, billed, variance, unpriced];
}
