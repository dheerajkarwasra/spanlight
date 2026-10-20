/**
 * The audit log's filters: what the URL holds, what the API and the CSV are asked for, and the
 * words and file name around them. Pure functions only, so the list and the CSV download always
 * carry the same filters.
 */
import type { AuditEvent, AuditFileFormat, AuditFilters } from "@/lib/api";

/** The filters as the URL keeps them; days are local (`2026-10-08`) and inclusive. */
export interface AuditFilterValues {
  action?: string | undefined;
  actor?: string | undefined;
  since?: string | undefined;
  until?: string | undefined;
  outcome?: AuditEvent["outcome"] | undefined;
  /** A project id: only events written in that project. */
  project?: string | undefined;
  /** Who acted: a signed-in person, an access token, an admin command or the app itself. */
  via?: NonNullable<AuditEvent["actor_type"]> | undefined;
}

const DAY_PATTERN = /^(\d{4})-(\d{2})-(\d{2})$/;

/** Whether `day` is a real calendar day written `YYYY-MM-DD` (not `2026-02-31`). */
export function isRealDay(day: string): boolean {
  return localMidnight(day) !== null;
}

/** The local midnight that starts `day` (offset by whole days), or null when it is not a date. */
function localMidnight(day: string, offsetDays = 0): Date | null {
  const match = DAY_PATTERN.exec(day);
  if (!match) {
    return null;
  }
  const [year, month, date] = [Number(match[1]), Number(match[2]), Number(match[3])];
  const result = new Date(year, month - 1, date + offsetDays);
  // `new Date(2026, 1, 31)` rolls over to March: refuse a day that is not on the calendar.
  const isReal = new Date(year, month - 1, date).getMonth() === month - 1;
  return isReal && !Number.isNaN(result.getTime()) ? result : null;
}

/**
 * The query the list and the CSV both take. `from` is the start of the first day and `to` the
 * start of the day after the last one (the API's `to` is exclusive), both as UTC instants, so
 * "Oct 1 – Oct 8" covers every event of those two local days. Filters that are not set are left
 * out.
 */
export function toAuditFilters(values: AuditFilterValues): AuditFilters {
  const filters: AuditFilters = {};
  if (values.action) {
    filters.action = values.action;
  }
  if (values.actor) {
    filters.actor_id = values.actor;
  }
  const from = values.since ? localMidnight(values.since) : null;
  if (from) {
    filters.from = from.toISOString();
  }
  const to = values.until ? localMidnight(values.until, 1) : null;
  if (to) {
    filters.to = to.toISOString();
  }
  if (values.outcome) {
    filters.outcome = values.outcome;
  }
  if (values.project) {
    filters.project_id = values.project;
  }
  if (values.via) {
    filters.actor_type = values.via;
  }
  return filters;
}

/** Whether any filter is set: "Clear filters" and the empty-state wording depend on it. */
export function hasAuditFilters(values: AuditFilterValues): boolean {
  return Boolean(
    values.action ||
    values.actor ||
    values.since ||
    values.until ||
    values.outcome ||
    values.project ||
    values.via,
  );
}

/** Why the two days cannot be applied, or null: the API refuses a range that ends before it starts. */
export function auditDateIssue(
  since: string | undefined,
  until: string | undefined,
): string | null {
  if (!since || !until) {
    return null;
  }
  const start = localMidnight(since);
  const end = localMidnight(until);
  if (!start || !end) {
    return "Enter a valid date.";
  }
  return start > end ? "The start date must be on or before the end date." : null;
}

const monthDay = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric" });
const monthDayYear = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  year: "numeric",
});

/** The date pill's text: "Any time", "Oct 1 – Oct 8, 2026", "From Oct 1, 2026" or "Until Oct 8, 2026". */
export function describeAuditDates(since: string | undefined, until: string | undefined): string {
  const start = since ? localMidnight(since) : null;
  const end = until ? localMidnight(until) : null;
  if (start && end) {
    if (start.getFullYear() === end.getFullYear()) {
      return `${monthDay.format(start)} – ${monthDayYear.format(end)}`;
    }
    return `${monthDayYear.format(start)} – ${monthDayYear.format(end)}`;
  }
  if (start) {
    return `From ${monthDayYear.format(start)}`;
  }
  if (end) {
    return `Until ${monthDayYear.format(end)}`;
  }
  return "Any time";
}

/** `2026-10-08`, today's local date, for the file name. */
function localDay(date: Date): string {
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${date.getFullYear()}-${month}-${day}`;
}

/** `audit-acme-ai-2026-10-09.csv` (or `.jsonl`). The slug is made safe for a file name. */
export function auditFilename(
  orgSlug: string,
  format: AuditFileFormat = "csv",
  now: Date = new Date(),
): string {
  const slug = orgSlug.replace(/[^a-zA-Z0-9_-]+/g, "-").replace(/^-+|-+$/g, "") || "org";
  return `audit-${slug}-${localDay(now)}.${format}`;
}
