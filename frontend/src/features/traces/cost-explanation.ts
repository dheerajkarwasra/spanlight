import type {
  CostLine,
  CostLineKind,
  ExplanationStatus,
  ServiceTier,
  Span,
  TierNote,
} from "@/lib/api";
import { formatInteger } from "@/lib/format";

/**
 * Pure helpers for the span cost card (Figma "Span cost card / *"). Money stays a decimal string
 * from the API to the screen: it is formatted by its digits, never through a float, so an audit
 * figure is shown exactly as the server computed it.
 */

/** True when a span carries any model-call data: worth a usage grid and a cost breakdown. */
export function hasModelCall(
  span: Pick<
    Span,
    | "model"
    | "provider"
    | "input_tokens"
    | "output_tokens"
    | "cached_tokens"
    | "cache_write_tokens"
    | "cost_usd"
  >,
): boolean {
  return (
    span.model !== null ||
    span.provider !== null ||
    span.input_tokens !== null ||
    span.output_tokens !== null ||
    span.cached_tokens !== null ||
    span.cache_write_tokens !== null ||
    span.cost_usd !== null
  );
}

export type StatusTone = "ok" | "warning" | "muted";

export interface StatusCopy {
  tone: StatusTone;
  /** The status chip. */
  title: string;
  /** The sentence under the header; null when there is nothing to add. */
  body: string | null;
}

const STATUS: Record<ExplanationStatus, { tone: StatusTone; title: string }> = {
  ok: { tone: "ok", title: "Matches" },
  mismatch: { tone: "warning", title: "Mismatch" },
  no_usage: { tone: "muted", title: "No usage" },
  no_price: { tone: "muted", title: "No price" },
  price_version_missing: { tone: "muted", title: "Price row gone" },
  override_deleted: { tone: "muted", title: "Price row gone" },
  cache_hit: { tone: "ok", title: "Cache hit" },
  usage_rollup: { tone: "muted", title: "Usage rollup" },
};

export const MISMATCH_BODY = "This is a bug in Spanlight. Report it with the span id.";

/** The chip and sentence of a status; the server's `message` is shown as written. */
export function statusCopy(status: ExplanationStatus, message: string | null): StatusCopy {
  const { tone, title } = STATUS[status];
  return { tone, title, body: status === "mismatch" ? MISMATCH_BODY : message };
}

const LINE_LABELS: Record<CostLineKind, string> = {
  uncached_input: "Uncached input",
  cache_read: "Cache read",
  cache_write_5m: "Cache write",
  cache_write_1h: "Cache write · 1 h",
  output: "Output",
  web_search: "Web search",
};

export interface FormattedLine {
  kind: string;
  quantity: string;
  rate: string;
  /** Null on a fee line, which no tier multiplies. */
  multiplier: string | null;
  usd: string;
  /** A zero line is kept (the five token classes always show) but dimmed. */
  dimmed: boolean;
}

export function formatLine(line: CostLine): FormattedLine {
  const perUnit = line.rate_unit === "usd_per_mtok" ? "MTok" : "1K requests";
  const fee = line.kind === "web_search";
  return {
    kind: LINE_LABELS[line.kind],
    quantity: formatInteger(line.quantity) ?? String(line.quantity),
    rate: `$${formatDecimal(line.rate, 2)} / ${perUnit}`,
    multiplier: fee ? null : `×${formatDecimal(line.multiplier, 0)}`,
    usd: isZero(line.usd) ? "$0" : `$${formatDecimal(line.usd, 6)}`,
    dimmed: line.quantity === 0,
  };
}

/**
 * The lines to show: every token class, and a fee line only when searches ran (a `$0` fee for
 * zero searches is noise, unlike a zero token class).
 */
export function visibleLines(lines: readonly CostLine[]): CostLine[] {
  return lines.filter((line) => line.kind !== "web_search" || line.quantity > 0);
}

/** An exact amount with at least six decimals: "0.06357000" → "$0.063570". */
export function formatExactUsd(value: string | null): string | null {
  return value === null ? null : `$${formatDecimal(value, 6)}`;
}

/** "standard · ×1", "priority · ×1 · unmodelled"; null when the tier was not reported. */
export function tierLabel(
  tier: ServiceTier | null,
  multiplier: string,
  note: TierNote | null,
): string | null {
  if (tier === null) {
    return null;
  }
  const parts = [tier, `×${formatDecimal(multiplier, 0)}`];
  if (note !== null) {
    parts.push(note === "commitment_billed" ? "commitment-billed" : "unmodelled");
  }
  return parts.join(" · ");
}

const RECONCILIATION_TAIL =
  "the difference is counted as an unmodelled modifier in the reconciliation report.";

/** Why a non-standard tier was priced at the standard rate (Figma "tier unmodelled" frames). */
export function tierNoteCopy(
  note: TierNote | null,
  provider: string | null,
  tier: ServiceTier | null,
): string | null {
  if (note === null) {
    return null;
  }
  if (note === "commitment_billed") {
    return (
      "Priced at the standard rate. Anthropic bills priority under a capacity commitment, so " +
      "these calls are not in the provider cost report; the reconciliation lists their " +
      "standard-rate cost as a priority-commitment line."
    );
  }
  if (provider === "openai" && tier === "priority") {
    return `Priced at the standard rate. OpenAI bills priority (Fast) per model; ${RECONCILIATION_TAIL}`;
  }
  const tierText = tier ?? "this";
  const providerText = provider ?? "this provider";
  return `Priced at the standard rate. Spanlight has no ${tierText} rate for ${providerText}; ${RECONCILIATION_TAIL}`;
}

/** "Output 812 includes 300 reasoning tokens, billed at the output rate." */
export function outputBreakdownNote(
  breakdown: { reasoning: number; other: number } | null,
  outputTokens: number | null,
): string | null {
  if (breakdown === null || outputTokens === null) {
    return null;
  }
  const output = formatInteger(outputTokens) ?? String(outputTokens);
  const reasoning = formatInteger(breakdown.reasoning) ?? String(breakdown.reasoning);
  return `Output ${output} includes ${reasoning} reasoning tokens, billed at the output rate.`;
}

/** "Usage was recorded: 60,510 input, 48,200 cached, 812 output."; null when incomplete. */
export function recordedUsageSentence(usage: {
  input_tokens: number | null;
  cached_tokens: number | null;
  output_tokens: number | null;
}): string | null {
  const { input_tokens: input, cached_tokens: cached, output_tokens: output } = usage;
  if (input === null || output === null) {
    return null;
  }
  const parts = [`${formatInteger(input) ?? input} input`];
  if (cached !== null) {
    parts.push(`${formatInteger(cached) ?? cached} cached`);
  }
  parts.push(`${formatInteger(output) ?? output} output`);
  return `Usage was recorded: ${parts.join(", ")}.`;
}

/** A stored `usage.unreported_reason` in words: "client_disconnected" → "client disconnected". */
export function reasonLabel(reason: string | null): string | null {
  return reason === null ? null : reason.replaceAll("_", " ");
}

export type VarianceTone = "within2" | "within10" | "over" | "unknown";

/** The band of a signed variance ratio: within 2 %, within 10 %, over, or unknown. */
export function varianceTone(ratio: number | null): VarianceTone {
  if (ratio === null || !Number.isFinite(ratio)) {
    return "unknown";
  }
  const size = Math.abs(ratio);
  if (size <= 0.02) {
    return "within2";
  }
  return size <= 0.1 ? "within10" : "over";
}

const DECIMAL = /^(-?)(\d+)(?:\.(\d+))?$/;

function isZero(value: string): boolean {
  return /^-?0*(?:\.0*)?$/.test(value);
}

/**
 * A decimal string with thousands separators and at least `minDecimals` decimals, keeping every
 * significant digit past that: ("0.075000", 2) → "0.075", ("1", 0) → "1".
 */
function formatDecimal(value: string, minDecimals: number): string {
  const match = DECIMAL.exec(value.trim());
  if (match === null) {
    return value;
  }
  const [, sign = "", whole = "0", fraction = ""] = match;
  const digits = fraction.replace(/0+$/, "").padEnd(minDecimals, "0");
  const grouped = whole.replace(/^0+(?=\d)/, "").replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return digits === "" ? `${sign}${grouped}` : `${sign}${grouped}.${digits}`;
}
