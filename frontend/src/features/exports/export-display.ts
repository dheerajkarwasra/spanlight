/**
 * How an export is written on screen: dates in the viewer's local time (like the trace list),
 * sizes and counts, and the words for each status and failure. Pure functions only. Every
 * formatter takes `now` so a test can pin it, and returns null for an unknown value so the
 * caller can show the shared "—" placeholder instead of a zero.
 */
import type { AttributionGroupBy, ExportFormat, TraceExport } from "@/lib/api";
import { formatInteger, formatRelativeTime } from "@/lib/format";

import { exportFilterEntries, exportFilterSummary } from "./export-filters";

/** Same local-time display as the trace list, written day-first: "7 Oct 2026, 14:20". */
const instantFormat = new Intl.DateTimeFormat("en-GB", {
  day: "numeric",
  month: "short",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

const monthDayFormat = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric" });
const monthDayYearFormat = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  year: "numeric",
});
const timeFormat = new Intl.DateTimeFormat("en-US", {
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

function parse(iso: string | null | undefined): Date | null {
  if (!iso) {
    return null;
  }
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? null : date;
}

/** "7 Oct 2026, 14:20" for the export dialog's summary. */
export function formatExportInstant(iso: string | null | undefined): string | null {
  const date = parse(iso);
  return date ? instantFormat.format(date) : null;
}

/**
 * The window of an export as a list row's heading: "Oct 8 – Oct 9, 2026", "Dec 28, 2025 – Jan 3,
 * 2026" across a year, or "Oct 9, 2026" inside one day.
 */
export function formatExportRange(
  from: string | null | undefined,
  to: string | null | undefined,
): string | null {
  const start = parse(from);
  const end = parse(to);
  if (!start || !end) {
    return null;
  }
  if (start.toDateString() === end.toDateString()) {
    return monthDayYearFormat.format(start);
  }
  if (start.getFullYear() === end.getFullYear()) {
    return `${monthDayFormat.format(start)} – ${monthDayYearFormat.format(end)}`;
  }
  return `${monthDayYearFormat.format(start)} – ${monthDayYearFormat.format(end)}`;
}

/** The full start and end for a tooltip on the range: "7 Oct 2026, 14:20 – 8 Oct 2026, 14:20". */
export function formatExportRangeDetail(
  from: string | null | undefined,
  to: string | null | undefined,
): string | null {
  const start = formatExportInstant(from);
  const end = formatExportInstant(to);
  return start && end ? `${start} – ${end}` : null;
}

/** "Oct 9, 10:41"; the year is added when it is not this year's. */
export function formatExportCreated(
  iso: string | null | undefined,
  now: Date = new Date(),
): string | null {
  const date = parse(iso);
  if (!date) {
    return null;
  }
  const day =
    date.getFullYear() === now.getFullYear()
      ? monthDayFormat.format(date)
      : monthDayYearFormat.format(date);
  return `${day}, ${timeFormat.format(date)}`;
}

/** "Oct 16"; the year is added when it is not this year's. */
export function formatExportDay(
  iso: string | null | undefined,
  now: Date = new Date(),
): string | null {
  const date = parse(iso);
  if (!date) {
    return null;
  }
  return date.getFullYear() === now.getFullYear()
    ? monthDayFormat.format(date)
    : monthDayYearFormat.format(date);
}

/** "in 7 days" or "2 days ago", next to the day a file expires. */
export function formatExportRelative(
  iso: string | null | undefined,
  now: Date = new Date(),
): string | null {
  return formatRelativeTime(iso, now);
}

/** "1,284", or null while unknown. */
export function formatExportRows(rows: number | null | undefined): string | null {
  return formatInteger(rows);
}

const SIZE_UNITS = ["B", "KB", "MB", "GB", "TB"] as const;

/** "412 KB", "46.1 MB", "2.4 MB": 1024-based, one decimal below 100 of a unit. Null while unknown. */
export function formatExportSize(bytes: number | null | undefined): string | null {
  if (bytes === null || bytes === undefined || !Number.isFinite(bytes) || bytes < 0) {
    return null;
  }
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < SIZE_UNITS.length - 1) {
    value /= 1024;
    unit += 1;
  }
  const label = SIZE_UNITS[unit] ?? "B";
  if (unit === 0 || value >= 100) {
    return `${Math.round(value)} ${label}`;
  }
  return `${value.toFixed(1)} ${label}`;
}

/** "12,904 traces" or "1 trace", for the number a dialog or a notice states. */
export function formatTraceCount(count: number): string {
  const text = formatInteger(count) ?? String(count);
  return `${text} ${count === 1 ? "trace" : "traces"}`;
}

/** "JSONL" or "CSV", as the format tag and the format cards write it. */
export function exportFormatLabel(format: ExportFormat): string {
  return format === "jsonl" ? "JSONL" : "CSV";
}

const GROUP_NAMES: Record<Exclude<AttributionGroupBy, "metadata">, string> = {
  trace_name: "trace name",
  user: "user",
  release: "release",
};

/**
 * What an export holds, under its range: "status: error · model: gpt-4.1" or "All traces" for
 * traces, "Attribution by customer_id · environment: production" for an attribution table.
 */
function exportSummary(entry: TraceExport): string {
  if (entry.kind !== "attribution") {
    return exportFilterSummary(entry.filters);
  }
  const filters = exportFilterEntries(entry.filters).map((item) => `${item.key}: ${item.value}`);
  return [`Attribution by ${attributionGroup(entry)}`, ...filters].join(" · ");
}

/** The metadata key itself, or "trace name", "user", "release". */
function attributionGroup(entry: TraceExport): string {
  return entry.group_by === "metadata" || entry.group_by === null
    ? (entry.key ?? "metadata")
    : GROUP_NAMES[entry.group_by];
}

/** "JSONL export, Oct 1 – Oct 8, 2026" or "Attribution CSV export, customer_id, Oct 1 – Oct 8". */
function downloadLabel(entry: TraceExport, range: string): string {
  const format = exportFormatLabel(entry.format);
  return entry.kind === "attribution"
    ? `Attribution ${format} export, ${attributionGroup(entry)}, ${range}`
    : `${format} export, ${range}`;
}

/** Everything a list row or tile writes about an export, ready to render. */
export interface ExportDescription {
  /** "Oct 8 – Oct 9, 2026". */
  range: string;
  /** The exact start and end, for a tooltip. */
  rangeDetail: string | null;
  /** "status: error · model: gpt-4.1", "All traces", or "Attribution by customer_id". */
  summary: string;
  /** Names the download button for assistive technology. */
  downloadLabel: string;
  rows: string | null;
  size: string | null;
  created: string;
  /** "Oct 16" and "in 7 days"; null while the export has no expiry. */
  expires: string | null;
  expiresRelative: string | null;
}

export function describeExport(entry: TraceExport, now: Date = new Date()): ExportDescription {
  const { from, to } = entry.filters;
  const range = formatExportRange(from, to) ?? "Unknown range";
  return {
    range,
    rangeDetail: formatExportRangeDetail(from, to),
    summary: exportSummary(entry),
    downloadLabel: downloadLabel(entry, range),
    rows: formatExportRows(entry.row_count),
    size: formatExportSize(entry.size_bytes),
    created: formatExportCreated(entry.created_at, now) ?? entry.created_at,
    expires: formatExportDay(entry.expires_at, now),
    expiresRelative: formatExportRelative(entry.expires_at, now),
  };
}
