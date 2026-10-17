import type {
  ExplainedKind,
  ExplainedLine,
  ListedCharge,
  ReconciliationDay,
  ReconciliationGroup,
  ReconciliationReport,
  ReconciliationTokens,
} from "@/lib/api";
import { formatInteger, pluralize } from "@/lib/format";

import {
  money,
  REASONS,
  varianceFigure,
  type Figure,
  type VarianceFigure,
} from "./reconciliation-figures";
import {
  dayLabel,
  formatDayRanges,
  formatTokenDifference,
  formatUsd,
} from "./reconciliation-format";
import { formatUtc } from "./source-model";

/**
 * Pure logic of the reconciliation page (Figma "Reconciliation / *"): the day rows with their
 * model groups, the chips of a day and the reason behind every unknown figure. A figure the
 * server sends as null is never shown as 0: it becomes "—" with one of the reasons below.
 */

/** The provisional rule as a sentence, for layouts where the chip has no tooltip. */
export const PROVISIONAL_NOTE =
  "Provisional: provider data can lag; this day is final 48 h after it ends and after a fetch past that point.";

export const PROVISIONAL_TOOLTIP =
  "Provider data can lag; this day is final 48 h after it ends and after a fetch past that point";

const COUNTED_NOT_PRICED =
  "— means counted, not priced: Spanlight knows how many calls or tokens but has no USD figure, so their cost stays in the residual.";

export interface DayChip {
  label: string;
  tooltip: string;
}

export interface TokenRow {
  label: string;
  provider: Figure;
  spanlight: Figure;
  difference: Figure;
}

export interface ExplainedRow {
  key: ExplainedKind;
  label: string;
  note: string;
  usd: Figure;
}

export interface GroupRow {
  key: string;
  name: string;
  /** "Provider model …" and "Spanlight model …". */
  models: string[];
  note: string | null;
  provider: Figure;
  spanlight: Figure;
  explained: Figure;
  residual: Figure;
  variance: VarianceFigure;
  tokens: TokenRow[];
  lines: ExplainedRow[];
}

export interface ChargeRow {
  description: string;
  usd: string;
}

export interface DayRow {
  day: string;
  label: string;
  chips: DayChip[];
  provider: Figure;
  spanlight: Figure;
  explained: Figure;
  residual: Figure;
  variance: VarianceFigure;
  /** "Settled · fetched Oct 10, 03:30 UTC · 2 model groups". */
  meta: string;
  groups: GroupRow[];
  credits: ChargeRow[];
  other: ChargeRow[];
  /** False while no fetch covers the day: its credits and other charges are unknown. */
  chargesKnown: boolean;
  footnote: string | null;
  /** The provisional rule and why figures are missing, as visible sentences (tile layout). */
  notes: string[];
}

export interface RenderRules {
  provisionalChip: boolean;
  /** The day's rollups are still being built ("Rollup pending"). */
  incompleteChip: boolean;
  retentionChip: boolean;
  /** Why the day's unknown figures are unknown; null when nothing is missing. */
  unknownReason: string | null;
}

const ROLLUP_PENDING_NOTE = "rollup pending";

/**
 * Whether spans of the day are past retention. The server marks such a day `incomplete` without
 * the "rollup pending" note, and lists a `beyond_retention` line in its groups; either says so.
 */
function pastRetention(day: ReconciliationDay): boolean {
  const flagged = day.incomplete && day.note !== ROLLUP_PENDING_NOTE;
  return (
    flagged ||
    day.groups.some((group) => group.explained.some((line) => line.kind === "beyond_retention"))
  );
}

/** Which chips a day shows and why its unknown figures are unknown. */
export function renderRules(day: ReconciliationDay): RenderRules {
  const retentionChip = pastRetention(day);
  let unknownReason: string | null = null;
  if (day.fetched_at === null) {
    unknownReason = REASONS.notFetched;
  } else if (day.note === ROLLUP_PENDING_NOTE) {
    unknownReason = REASONS.rollupPending;
  } else if (retentionChip) {
    unknownReason = REASONS.notRetained;
  }
  return {
    provisionalChip: day.provisional,
    incompleteChip: day.note === ROLLUP_PENDING_NOTE,
    retentionChip,
    unknownReason,
  };
}

function dayChips(rules: RenderRules): DayChip[] {
  const chips: DayChip[] = [];
  if (rules.provisionalChip) {
    chips.push({ label: "Provisional", tooltip: PROVISIONAL_TOOLTIP });
  }
  if (rules.incompleteChip) {
    chips.push({ label: "Rollup pending", tooltip: REASONS.rollupPending });
  }
  if (rules.retentionChip) {
    chips.push({ label: "Spans no longer retained", tooltip: REASONS.notRetained });
  }
  return chips;
}

/** Why each side of one day is unknown: the provider's, then Spanlight's. */
interface SideReasons {
  provider: string | null;
  spanlight: string | null;
}

/** Null for a side that was read; a missing figure on it then has no price. */
function sideReasons(day: ReconciliationDay): SideReasons {
  let spanlight: string | null = null;
  if (day.note === ROLLUP_PENDING_NOTE) {
    spanlight = REASONS.rollupPending;
  } else if (pastRetention(day)) {
    spanlight = REASONS.notRetained;
  }
  return { provider: day.fetched_at === null ? REASONS.notFetched : null, spanlight };
}

/** Why a Spanlight figure of a side that was read is null: no call at all, or none priced. */
function unpricedReason(groups: readonly ReconciliationGroup[]): string {
  const traced = groups.some((group) => group.spanlight_models.length > 0);
  return traced ? REASONS.noPrice : REASONS.noCalls;
}

/** Why a comparison (explained, residual, variance, a difference) is missing: a missing side. */
function comparisonReason(sides: SideReasons): string {
  return sides.provider ?? sides.spanlight ?? REASONS.notFetched;
}

function varianceOf(ratio: string | null, residual: string | null, sides: SideReasons) {
  return varianceFigure(
    ratio,
    residual === null ? comparisonReason(sides) : REASONS.noProviderAmount,
  );
}

const TOKEN_CLASSES: readonly [keyof ReconciliationTokens, string][] = [
  ["uncached_input", "Uncached input"],
  ["cache_read", "Cache read"],
  ["cache_write_5m", "Cache write · 5 min"],
  ["cache_write_1h", "Cache write · 1 h"],
  ["output", "Output"],
];

function count(value: number | null, reason: string): Figure {
  return value === null
    ? { text: null, reason }
    : { text: formatInteger(value) ?? String(value), reason: null };
}

function tokenRows(group: ReconciliationGroup, sides: SideReasons): TokenRow[] {
  return TOKEN_CLASSES.map(([key, label]) => {
    const provider = group.provider_tokens[key];
    const spanlight = group.spanlight_tokens[key];
    const known = provider !== null && spanlight !== null;
    return {
      label,
      provider: count(provider, sides.provider ?? REASONS.notFetched),
      spanlight: count(spanlight, sides.spanlight ?? REASONS.noPrice),
      difference: known
        ? { text: formatTokenDifference(provider - spanlight), reason: null }
        : { text: null, reason: comparisonReason(sides) },
    };
  });
}

export const EXPLAINED_LABELS: Record<ExplainedKind, string> = {
  batch_spend: "Batch spend",
  server_tool_fees: "Server-tool fees",
  unpriced_spans: "Unpriced spans",
  usage_not_reported: "Usage not reported",
  untraced_traffic: "Untraced traffic",
  priority_commitment: "Priority commitment",
  unmodelled_modifiers: "Unmodelled modifiers",
  beyond_retention: "Beyond retention",
};

function explainedRow(line: ExplainedLine): ExplainedRow {
  return {
    key: line.kind,
    label: EXPLAINED_LABELS[line.kind],
    note: line.note,
    usd: money(line.usd, REASONS.notPriced),
  };
}

function modelsLine(side: string, models: readonly string[]): string {
  return `${side} ${models.length === 1 ? "model" : "models"} ${models.length > 0 ? models.join(", ") : "none"}`;
}

function groupRow(group: ReconciliationGroup, sides: SideReasons): GroupRow {
  const compared = comparisonReason(sides);
  return {
    key: group.group,
    name: group.group,
    models: [
      modelsLine("Provider", group.provider_models),
      modelsLine("Spanlight", group.spanlight_models),
    ],
    note: group.note,
    provider: money(group.provider_usd, sides.provider ?? REASONS.notFetched),
    spanlight: money(group.spanlight_usd, sides.spanlight ?? unpricedReason([group])),
    explained: money(group.explained_usd, compared),
    residual: money(group.residual_usd, compared, true),
    variance: varianceOf(group.variance_ratio, group.residual_usd, sides),
    tokens: tokenRows(group, sides),
    lines: group.explained.map(explainedRow),
  };
}

function charges(list: readonly ListedCharge[]): ChargeRow[] {
  return list.map((charge) => ({ description: charge.description, usd: formatUsd(charge.usd) }));
}

function dayMeta(day: ReconciliationDay): string {
  const parts = [
    day.provisional ? "Provisional" : "Settled",
    day.fetched_at === null ? "not fetched yet" : `fetched ${formatUtc(day.fetched_at)}`,
    pluralize(day.groups.length, "model group"),
  ];
  if (day.unattributed_calls > 0) {
    parts.push(
      `${pluralize(day.unattributed_calls, "call")} named no provider, so no source bills them`,
    );
  }
  return parts.join(" · ");
}

function dayFootnote(day: ReconciliationDay): string | null {
  const parts: string[] = [];
  if (day.fetched_at !== null && day.credits.length === 0 && day.other.length === 0) {
    parts.push("No credits or other provider charges on this day.");
  }
  if (day.groups.some((group) => group.explained.some((line) => line.usd === null))) {
    parts.push(COUNTED_NOT_PRICED);
  }
  return parts.length > 0 ? parts.join(" ") : null;
}

function dayNotes(day: ReconciliationDay): string[] {
  const { provisionalChip, unknownReason } = renderRules(day);
  return [provisionalChip ? PROVISIONAL_NOTE : null, unknownReason].filter((note) => note !== null);
}

function dayRow(day: ReconciliationDay): DayRow {
  const sides = sideReasons(day);
  const compared = comparisonReason(sides);
  return {
    day: day.day,
    label: dayLabel(day.day),
    chips: dayChips(renderRules(day)),
    provider: money(day.provider_usd, sides.provider ?? REASONS.notFetched),
    spanlight: money(day.spanlight_usd, sides.spanlight ?? unpricedReason(day.groups)),
    explained: money(day.explained_usd, compared),
    residual: money(day.residual_usd, compared, true),
    variance: varianceOf(day.variance_ratio, day.residual_usd, sides),
    meta: dayMeta(day),
    groups: day.groups.map((group) => groupRow(group, sides)),
    credits: charges(day.credits),
    other: charges(day.other),
    chargesKnown: day.fetched_at !== null,
    footnote: dayFootnote(day),
    notes: dayNotes(day),
  };
}

/** The report's days as table rows, newest first, each with its groups and listed charges. */
export function flattenDays(report: ReconciliationReport): DayRow[] {
  return [...report.days].sort((a, b) => b.day.localeCompare(a.day)).map(dayRow);
}

/** The days a failing source leaves without provider figures, and those it leaves provisional. */
export function pendingDayRanges(report: ReconciliationReport): {
  unfetched: string | null;
  provisional: string | null;
} {
  const unfetched = report.days.filter((day) => day.fetched_at === null).map((day) => day.day);
  const provisional = report.days
    .filter((day) => day.fetched_at !== null && day.provisional)
    .map((day) => day.day);
  return { unfetched: formatDayRanges(unfetched), provisional: formatDayRanges(provisional) };
}

export { summaryTiles, type SummaryTile } from "./reconciliation-summary";
export { REASONS, type Figure, type VarianceFigure } from "./reconciliation-figures";
