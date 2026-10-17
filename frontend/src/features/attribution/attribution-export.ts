/**
 * The attribution export as pure functions: the request body built from the page's view, and the
 * words the dialog and its queued state write about it.
 */
import { formatExportInstant } from "@/features/exports";
import type { AttributionExportInput, AttributionGroupBy, AttributionResponse } from "@/lib/api";
import type { ResolvedRange } from "@/lib/time-range";

import { GROUP_BY_KINDS } from "./attribution-grid";

const HOUR_MS = 3_600_000;
const DAY_MS = 24 * HOUR_MS;

/** What the attribution view would export now: its group, window, environment and source. */
export interface AttributionExportView {
  groupBy: AttributionGroupBy;
  key: string | undefined;
  range: ResolvedRange;
  environment: string | undefined;
  /** The loaded first page, whose window is the one the export reads; undefined until loaded. */
  page: AttributionResponse | undefined;
}

const UTC_MONTH = new Intl.DateTimeFormat("en-US", { month: "short", timeZone: "UTC" });

/** "16 Sep 2026", a UTC day. */
function utcDay(date: Date): string {
  return `${date.getUTCDate()} ${UTC_MONTH.format(date)} ${date.getUTCFullYear()}`;
}

export function groupLabel(groupBy: AttributionGroupBy): string {
  return GROUP_BY_KINDS.find((kind) => kind.value === groupBy)?.label ?? groupBy;
}

export function sourceOf(view: AttributionExportView): "raw" | "rollup" {
  return view.page?.source ?? (view.range.durationMs <= DAY_MS ? "raw" : "rollup");
}

/** The window the export reads: the server's widened UTC days once the page has loaded. */
function windowOf(view: AttributionExportView): { from: string; to: string } {
  return view.page?.window ?? view.range;
}

/** "customer_id · 30 days · CSV" or "Trace name · 24 hours · CSV", for the queued state. */
export function attributionExportTitle(view: AttributionExportView): string {
  const { from, to } = windowOf(view);
  const span = Date.parse(to) - Date.parse(from);
  const hours = Math.max(1, Math.round(span / HOUR_MS));
  const days = Math.round(span / DAY_MS);
  const length = hours <= 24 ? `${hours} ${hours === 1 ? "hour" : "hours"}` : `${days} days`;
  const group = view.groupBy === "metadata" ? (view.key ?? "metadata") : groupLabel(view.groupBy);
  return `${group} · ${length} · CSV`;
}

/**
 * "16 Sep 2026 – 15 Oct 2026" for the rollup's UTC days (`to` is exclusive, so the last day shown
 * is the day before it); the viewer's local instants for a live window or before the page loads.
 */
export function exportWindowText(view: AttributionExportView): string {
  const { page, range } = view;
  if (page && sourceOf(view) === "rollup") {
    const lastDay = new Date(Date.parse(page.window.to) - 1);
    return `${utcDay(new Date(page.window.from))} – ${utcDay(lastDay)}`;
  }
  return `${formatExportInstant(range.from) ?? "Unknown"} – ${formatExportInstant(range.to) ?? "Unknown"}`;
}

/** The `POST /exports` body: the view's window and environment; `key` only for metadata. */
export function toAttributionExportInput(view: AttributionExportView): AttributionExportInput {
  return {
    kind: "attribution",
    format: "csv",
    group_by: view.groupBy,
    ...(view.groupBy === "metadata" && view.key ? { key: view.key } : {}),
    filters: {
      from: view.range.from,
      to: view.range.to,
      ...(view.environment ? { environment: view.environment } : {}),
    },
  };
}
