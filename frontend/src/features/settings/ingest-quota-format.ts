import type { IngestLimit, IngestQuota } from "@/lib/api";
import { formatInteger } from "@/lib/format";

export type QuotaState = "unlimited" | "under" | "exhausted";

export interface QuotaRow {
  label: string;
  state: QuotaState;
  /** "48,213 of 1,000,000 spans"; null when unlimited (the card shows "—" with a reason). */
  text: string | null;
  /** used / limit, capped at 1 for the meter; null when unlimited. */
  ratio: number | null;
  source: string;
}

export const NO_QUOTA_REASON = "No daily quota on this instance";
export const RESETS_TEXT = "Resets at 00:00 UTC";
export const EXHAUSTED_TEXT = "Quota used up; new spans are refused until 00:00 UTC.";
export const FOOTER_LINES = [
  "Counts spans sent through the SDK and OTLP; gateway calls are governed by the gateway key limits.",
  "To raise this project's quota, ask the operator of this instance.",
] as const;

const UNITS = [
  ["GiB", 1024 ** 3],
  ["MiB", 1024 ** 2],
  ["KiB", 1024],
] as const;

/** "5 GiB", "211.6 MiB", "512 bytes": the largest binary unit, one decimal at most. */
export function formatBytes(bytes: number): string {
  for (const [unit, size] of UNITS) {
    if (bytes >= size) {
      const value = Math.round((bytes / size) * 10) / 10;
      return `${value.toLocaleString("en-US", { maximumFractionDigits: 1 })} ${unit}`;
    }
  }
  return `${formatInteger(bytes) ?? String(bytes)} bytes`;
}

export function sourceLabel(source: IngestLimit["source"]): string {
  return source === "project" ? "Set for this project" : "Instance default";
}

function row(
  label: string,
  used: number,
  limit: IngestLimit,
  format: (value: number) => string,
  unit: string,
): QuotaRow {
  if (limit.value === null) {
    return {
      label,
      state: "unlimited",
      text: null,
      ratio: null,
      source: sourceLabel(limit.source),
    };
  }
  const ratio = Math.min(1, used / limit.value);
  return {
    label,
    state: used >= limit.value ? "exhausted" : "under",
    text: `${format(used)} of ${format(limit.value)}${unit}`,
    ratio,
    source: sourceLabel(limit.source),
  };
}

/** The spans row, then the bytes row, as the card shows them. */
export function quotaRows(quota: IngestQuota): [QuotaRow, QuotaRow] {
  const spans = (value: number) => formatInteger(value) ?? String(value);
  return [
    row("Spans today", quota.today.spans, quota.limits.spans_per_day, spans, " spans"),
    row("Bytes today", quota.today.bytes, quota.limits.bytes_per_day, formatBytes, ""),
  ];
}

/** "1,000 spans per second", or null when the rate is unlimited. */
export function rateLine(limit: number | null): string | null {
  return limit === null ? null : `${formatInteger(limit) ?? String(limit)} spans per second`;
}
