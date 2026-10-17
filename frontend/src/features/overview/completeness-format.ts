import type { Completeness, TelemetryLoss } from "@/lib/api";
import { formatInteger, pluralize } from "@/lib/format";

/** U+00A0, so "99.98 %" never breaks before the unit. */
const NBSP = " ";

/** The card's description and the start of every tooltip. */
export const COMPLETENESS_DEFINITION =
  "Share of spans that reached storage: stored ÷ (stored + every span lost on the way)";

export const NO_RATIO_REASON = "nothing was stored or lost in this window, so there is no ratio";

/**
 * Only the Python SDK from 0.2.0 reports what it dropped or failed to send; an older SDK, an OTLP
 * exporter or a custom sender reports nothing, so a zero from the SDK is unknown, not none.
 */
export const SDK_UNREPORTED_REASON =
  "Reported by the Python SDK from 0.2.0; other senders don't report their losses";

export const LOWER_BOUND_REASON =
  "lower bound: a span the SDK reports as failed may also be counted by the server as rejected, so the true share can be a little higher.";

/** The API's ratio: an integer part of 0 or 1, then up to four (or more) places. */
const RATIO = /^(0|1)(?:\.(\d+))?$/;

/** The largest share shown while any span was lost: the API rounds down to four places. */
const BELOW_WHOLE = "0.9999";

/**
 * `"0.9987"` → "99.87 %", `"0"` → "0 %", `null` → "—". Places past the fourth are cut, never
 * rounded, so a ratio under 1 can never read 100 %. A value outside 0 to 1 is "—".
 */
export function formatCompleteness(value: string | null): string {
  const match = value === null ? null : RATIO.exec(value);
  if (!match) {
    return "—";
  }
  const whole = match[1] ?? "0";
  const places = (match[2] ?? "").padEnd(4, "0").slice(0, 4);
  if (whole === "1" && Number(places) > 0) {
    return "—";
  }
  const integer = Number(whole) * 100 + Number(places.slice(0, 2));
  const decimals = places.slice(2).replace(/0+$/, "");
  return `${String(integer)}${decimals ? `.${decimals}` : ""}${NBSP}%`;
}

export function totalLost(losses: readonly TelemetryLoss[]): number {
  return losses.reduce((sum, loss) => sum + loss.count, 0);
}

/** The card's ratio, or null for "—". While any span was lost it never reads 100 %. */
export function completenessText(result: Completeness): string | null {
  if (result.completeness === null) {
    return null;
  }
  const lost = totalLost(result.losses) > 0;
  const value = lost && result.completeness.startsWith("1") ? BELOW_WHOLE : result.completeness;
  const text = formatCompleteness(value);
  return text === "—" ? null : text;
}

/**
 * The header's info tooltip: the definition first, then the SDK/ingest overlap for a lower bound or
 * why there is no ratio.
 */
export function completenessReason(result: Completeness): string {
  if (result.completeness === null) {
    return `${COMPLETENESS_DEFINITION}. ${NO_RATIO_REASON.charAt(0).toUpperCase()}${NO_RATIO_REASON.slice(1)}.`;
  }
  return result.lower_bound
    ? `${COMPLETENESS_DEFINITION}. ${LOWER_BOUND_REASON}`
    : `${COMPLETENESS_DEFINITION}.`;
}

export interface LossTile {
  source: string;
  label: string;
  /** Spans lost; null when the source reported nothing and its silence proves nothing. */
  count: number | null;
  /** "validation 9 · too_old 3", largest first, "none", or "none reported". */
  detail: string;
  /** Why `count` is unknown, or null when it is known. */
  unknownReason: string | null;
}

interface Source {
  source: string;
  label: string;
  /** Why no report is unknown rather than zero; null for a source the server counts itself. */
  silentReason: string | null;
}

const SOURCES: readonly Source[] = [
  { source: "gateway", label: "Gateway", silentReason: null },
  { source: "ingest", label: "Ingest rejected", silentReason: null },
  { source: "sdk", label: "SDK dropped or failed", silentReason: SDK_UNREPORTED_REASON },
];

function lossTile({ source, label, silentReason }: Source, losses: readonly TelemetryLoss[]) {
  const reasons = losses
    .filter((loss) => loss.source === source && loss.count > 0)
    .sort((a, b) => b.count - a.count);
  if (reasons.length === 0) {
    return silentReason === null
      ? { source, label, count: 0, detail: "none", unknownReason: null }
      : { source, label, count: null, detail: "none reported", unknownReason: silentReason };
  }
  return {
    source,
    label,
    count: totalLost(reasons),
    detail: reasons.map((loss) => `${loss.reason} ${formatInteger(loss.count) ?? "0"}`).join(" · "),
    unknownReason: null,
  };
}

/** One tile per source: the three known ones always, in order, then any other the API sent. */
export function lossTiles(losses: readonly TelemetryLoss[]): LossTile[] {
  const known = new Set(SOURCES.map((entry) => entry.source));
  const extra = [...new Set(losses.map((loss) => loss.source))].filter(
    (source) => !known.has(source),
  );
  return [
    ...SOURCES,
    ...extra.map((source) => ({ source, label: source, silentReason: null })),
  ].map((source) => lossTile(source, losses));
}

/**
 * "128,406 spans stored · 22 lost", "… · no losses reported", or the empty window's sentence. With
 * nothing reported it does not claim nothing was lost: senders other than the SDK report nothing.
 */
export function storedLine(result: Completeness): string {
  if (result.completeness === null) {
    return "No spans stored or lost in this window";
  }
  const lost = totalLost(result.losses);
  const stored = pluralize(result.stored_spans, "span");
  if (lost === 0) {
    return `${stored} stored · no losses reported`;
  }
  return `${stored} stored · ${formatInteger(lost) ?? "0"} lost`;
}
