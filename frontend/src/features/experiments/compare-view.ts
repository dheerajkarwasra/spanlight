/**
 * Pure logic of the run comparison: score kinds and formats, the change of each item, the sort
 * by the largest change, and the trade-off sentence. Every score is higher-is-better.
 */
import type { ItemDelta, ItemScore, PairedTest, ScoreConfig, ScoreDelta } from "@/lib/api";
import { formatInteger, parseMoney } from "@/lib/format";
import { formatScoreNumber, type ScoreValueParts } from "@/lib/score-format";

export type ScoreKind = "numeric" | "boolean" | "categorical";

/** How one item moved on one score between run A and run B. */
export type ItemChange = "improved" | "regressed" | "unchanged" | "changed" | "not_comparable";

export type Tone = "good" | "bad" | "neutral";

export type CategoryTone = "success" | "warning" | "danger";

const MINUS = "−";

/** The kind of a score: its config's data type, else what its values look like. */
export function scoreKind(
  name: string,
  configs: readonly ScoreConfig[] | undefined,
  samples: readonly unknown[],
): ScoreKind {
  const config = configs?.find((candidate) => candidate.name === name);
  if (config) {
    return config.data_type;
  }
  if (samples.some((value) => typeof value === "boolean")) {
    return "boolean";
  }
  return samples.some((value) => typeof value === "string") ? "categorical" : "numeric";
}

function signed(value: number, text: string): string {
  if (value > 0) {
    return `+${text}`;
  }
  return value < 0 ? `${MINUS}${text}` : text;
}

/** One item's score as the shared score formatting reads it (a boolean is 1 or 0). */
export function itemScoreParts(value: ItemScore): ScoreValueParts {
  if (typeof value === "string") {
    return { value: null, string_value: value };
  }
  return { value: typeof value === "boolean" ? Number(value) : value, string_value: null };
}

/**
 * A run's mean: "0.8667" (four decimals at most, like every score), or "96%" for a yes / no. The
 * percent rounds down, so "100%" means every item passed (199 of 200 reads "99%").
 */
export function formatMean(mean: number | null, kind: ScoreKind): string | null {
  if (mean === null || !Number.isFinite(mean)) {
    return null;
  }
  // The epsilon keeps a float like 0.29 * 100 = 28.999… from flooring to 28.
  return kind === "boolean" ? `${Math.floor(mean * 100 + 1e-9)}%` : formatScoreNumber(mean);
}

/**
 * A score change: "+0.08", "−0.53", or "+6 pts" for a yes / no score's mean. A change too small
 * for two decimals keeps its sign and shows the API's four ("+0.0030"), never a bare "0.00"; one
 * too small for four reads "+<0.0001".
 */
export function formatScoreDelta(delta: number | null, kind: ScoreKind): string | null {
  if (delta === null || !Number.isFinite(delta)) {
    return null;
  }
  if (kind === "boolean") {
    const points = Math.round(Math.abs(delta) * 100);
    return signed(delta, points === 0 && delta !== 0 ? "<1 pt" : `${points} pts`);
  }
  if (delta !== 0 && Math.abs(delta) < 0.00005) {
    return signed(delta, "<0.0001");
  }
  const digits = delta !== 0 && Math.abs(delta) < 0.005 ? 4 : 2;
  return signed(delta, Math.abs(delta).toFixed(digits));
}

/** One item's yes / no change as words: "No → Yes". */
export function formatBooleanChange(before: unknown, after: unknown): string | null {
  if (typeof before !== "boolean" || typeof after !== "boolean") {
    return null;
  }
  return `${before ? "Yes" : "No"} \u2192 ${after ? "Yes" : "No"}`;
}

/**
 * "+$0.012", "−$0.30": four decimals at most, trailing zeros dropped past the cents. A change below
 * a hundredth of a cent reads "+<$0.0001", never "$0.00".
 */
export function formatMoneyDelta(value: string | number | null): string | null {
  const amount = typeof value === "number" ? value : parseMoney(value);
  if (amount === null) {
    return null;
  }
  if (amount !== 0 && Math.abs(amount) < 0.00005) {
    return signed(amount, "<$0.0001");
  }
  const fixed = Math.abs(amount)
    .toFixed(4)
    .replace(/0{1,2}$/, "");
  return signed(Number(amount.toFixed(4)), `$${fixed}`);
}

/** A latency in ms or s, as the compare tiles read: "820 ms", "1.9 s". */
export function formatLatency(ms: number | null): string | null {
  if (ms === null || !Number.isFinite(ms)) {
    return null;
  }
  return Math.abs(ms) < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`;
}

/** "+310 ms", "−1.2 s". */
export function formatMsDelta(ms: number | null): string | null {
  const text = formatLatency(ms === null ? null : Math.abs(ms));
  return text === null || ms === null ? null : signed(Math.round(ms), text);
}

/** Higher scores are better; for cost, latency and errors lower is better. */
export function deltaTone(delta: number | null, higherIsBetter: boolean): Tone {
  if (delta === null || delta === 0 || !Number.isFinite(delta)) {
    return "neutral";
  }
  return delta > 0 === higherIsBetter ? "good" : "bad";
}

/** How one item moved on `score`. */
export function itemChange(item: ItemDelta, score: string): ItemChange {
  // An item edited between the runs is scored against different references: not a fair pair.
  if (item.changed === true) {
    return "not_comparable";
  }
  const before = item.a?.scores[score];
  const after = item.b?.scores[score];
  if (before === undefined || after === undefined) {
    return "not_comparable";
  }
  const delta = item.deltas[score] ?? null;
  if (delta === null) {
    return before === after ? "unchanged" : "changed";
  }
  if (delta > 0) {
    return "improved";
  }
  return delta < 0 ? "regressed" : "unchanged";
}

/** The items that got worse on `score`: a lower number, or Yes → No (booleans carry deltas). */
export function regressionsOnly(items: readonly ItemDelta[], score: string): ItemDelta[] {
  return items.filter((item) => itemChange(item, score) === "regressed");
}

function rank(change: ItemChange): number {
  if (change === "not_comparable") {
    return 2;
  }
  return change === "unchanged" ? 1 : 0;
}

/**
 * Items by the largest absolute change of `score` first; unchanged items next, then the items
 * that cannot be compared (one side or its score missing, or edited between the runs). Ties keep
 * the API's order.
 */
export function sortByChange(items: readonly ItemDelta[], score: string): ItemDelta[] {
  return items
    .map((item, index) => ({
      item,
      index,
      group: rank(itemChange(item, score)),
      size: Math.abs(item.deltas[score] ?? 0),
    }))
    .sort((x, y) => x.group - y.group || y.size - x.size || x.index - y.index)
    .map(({ item }) => item);
}

export interface ChangeCounts {
  changed: number;
  unchanged: number;
  notComparable: number;
  /** Items with this score in both runs. */
  scoredBoth: number;
  /** Items present in both runs. */
  inBoth: number;
}

export function changeCounts(items: readonly ItemDelta[], score: string): ChangeCounts {
  const counts: ChangeCounts = {
    changed: 0,
    unchanged: 0,
    notComparable: 0,
    scoredBoth: 0,
    inBoth: 0,
  };
  for (const item of items) {
    if (item.a && item.b) {
      counts.inBoth += 1;
    }
    const change = itemChange(item, score);
    if (change === "not_comparable") {
      counts.notComparable += 1;
      continue;
    }
    counts.scoredBoth += 1;
    if (change === "unchanged") {
      counts.unchanged += 1;
    } else {
      counts.changed += 1;
    }
  }
  return counts;
}

/**
 * The order and tone of a categorical score's categories: the config's order when it has one
 * (best first), else by how often they occur. First reads as good, last as bad.
 */
export function categoryOrder(
  configured: readonly string[],
  counts: readonly (Record<string, number> | null | undefined)[],
): { name: string; tone: CategoryTone }[] {
  const totals = new Map<string, number>();
  for (const record of counts) {
    for (const [name, count] of Object.entries(record ?? {})) {
      totals.set(name, (totals.get(name) ?? 0) + count);
    }
  }
  const seen = [...totals.keys()].filter((name) => !configured.includes(name));
  seen.sort((x, y) => (totals.get(y) ?? 0) - (totals.get(x) ?? 0) || x.localeCompare(y));
  const names = [...configured.filter((name) => totals.has(name)), ...seen];
  return names.map((name, index) => ({
    name,
    tone:
      index === 0 && names.length > 1
        ? "success"
        : index === names.length - 1 && names.length > 1
          ? "danger"
          : "warning",
  }));
}

/**
 * How far a cost delta can be trusted. A run with unpriced calls has a lower-bound total: when B
 * has some, B costs at least the delta more; when A has some, the direction is unknown.
 */
export type CostCertainty = "exact" | "at_least" | "partial";

export function costCertainty(aUnpriced: boolean, bUnpriced: boolean): CostCertainty {
  if (aUnpriced) {
    return "partial";
  }
  return bUnpriced ? "at_least" : "exact";
}

export interface TradeOff {
  /** The score the line leads with; null when the runs have no score. */
  score: string | null;
  kind: ScoreKind;
  scoreDelta: number | null;
  costDelta: string | null;
  costCertainty: CostCertainty;
  latencyP95Delta: number | null;
}

function costPart(delta: string | null, certainty: CostCertainty): string {
  const cost = formatMoneyDelta(delta);
  if (cost === null) {
    return "cost unknown";
  }
  if (certainty === "partial") {
    return "cost partly unknown";
  }
  if (certainty === "at_least") {
    return `costs at least ${cost}`;
  }
  return parseMoney(delta) === 0 ? "costs the same" : `costs ${cost}`;
}

/** "B scores +0.08 on relevance, costs +$0.012, p95 latency +310 ms." Unknown parts say so. */
export function formatTradeOff({
  score,
  kind,
  scoreDelta,
  costDelta,
  costCertainty: certainty,
  latencyP95Delta,
}: TradeOff): string {
  const parts: string[] = [];
  if (score !== null) {
    const delta = formatScoreDelta(scoreDelta, kind);
    if (kind === "categorical" || delta === null) {
      parts.push(`has no average to compare on ${score}`);
    } else {
      parts.push(scoreDelta === 0 ? `scores the same on ${score}` : `scores ${delta} on ${score}`);
    }
  }
  parts.push(costPart(costDelta, certainty));
  const latency = formatMsDelta(latencyP95Delta);
  parts.push(latency === null ? "p95 latency unknown" : `p95 latency ${latency}`);
  return `B ${parts.join(", ")}.`;
}

/** Below this many scored items an interval is still shown, flagged "few items". */
export const FEW_ITEMS = 10;

/**
 * A pass rate's 95% interval: "95% CI 64–88%, n = 50"; null without one. The low bound rounds
 * down and the high bound up, so the printed range always contains the interval.
 */
export function formatInterval(low: string | null, high: string | null, n: number): string | null {
  if (low === null || high === null) {
    return null;
  }
  // The bounds are 4-place decimals: whole basis points keep floor and ceil exact.
  const lowBp = Math.round(Number(low) * 10_000);
  const highBp = Math.round(Number(high) * 10_000);
  if (!Number.isFinite(lowBp) || !Number.isFinite(highBp)) {
    return null;
  }
  const range = `${Math.floor(lowBp / 100)}\u2013${Math.ceil(highBp / 100)}%`;
  return `95% CI ${range}, n = ${formatInteger(n) ?? n}`;
}

function verdict(test: PairedTest): string {
  const name = test.kind === "mcnemar" ? "exact McNemar" : "sign test";
  return test.significant
    ? `Significant (${name}, p = ${test.p_value}).`
    : `Not significant (${name}, p = ${test.p_value}): the runs may not differ.`;
}

function movement(test: PairedTest): string {
  const up = formatInteger(test.improved);
  const down = formatInteger(test.regressed);
  return test.kind === "mcnemar"
    ? `${formatInteger(test.n)} flipped: ${up} up, ${down} down`
    : `${up} up, ${down} down, ${formatInteger(test.unchanged)} unchanged`;
}

/**
 * The paired test under a score's delta on Compare (spec 7.2, 7.3). Null for a categorical score,
 * or with no test and nothing more to say. The tone is always neutral: a significant change is
 * not a verdict on which run is better, and "not significant" is not a failure.
 */
export function testSentence(
  delta: ScoreDelta,
  kind: ScoreKind,
  scoredBoth: number,
): { text: string; tone: Tone } | null {
  if (kind === "categorical") {
    return null;
  }
  const test = delta.test;
  if (test === null) {
    if (scoredBoth === 0) {
      return { text: "Nothing to compare: no item is scored in both runs.", tone: "neutral" };
    }
    return scoredBoth < 2
      ? {
          text: "No significance test: fewer than 2 items carry this score in both runs.",
          tone: "neutral",
        }
      : null;
  }
  if (test.n === 0) {
    return { text: "No items changed.", tone: "neutral" };
  }
  const shown = test.kind === "mcnemar" ? "boolean" : "numeric";
  const mean = (value: number | null) => formatMean(value, shown) ?? "unknown";
  const label = test.kind === "mcnemar" ? "Pass rate" : "Mean";
  const items = formatInteger(test.n + test.unchanged);
  const text = `${label} ${mean(delta.mean_a)} \u2192 ${mean(delta.mean_b)} on ${items} items (${movement(test)}). ${verdict(test)}`;
  return { text, tone: "neutral" };
}
