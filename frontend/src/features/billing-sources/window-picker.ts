/**
 * The reconciliation window (spec §8.3): whole UTC days, at most 31 of them, ending today at the
 * latest. The URL keeps the first and last day (`?first=2026-10-01&last=2026-10-14`, inclusive);
 * the API takes `from` and `to` as instants, `to` being the midnight after the last day. These
 * checks mirror the server's, so a window it would refuse is caught in the picker.
 */

export const MAX_WINDOW_DAYS = 31;
export const DEFAULT_WINDOW_DAYS = 14;

const DAY_MS = 86_400_000;
const DAY_PATTERN = /^\d{4}-\d{2}-\d{2}$/;

export const WINDOW_ERRORS = {
  invalid: "Pick a first and a last day.",
  order: "The first day must come before the last day.",
  tooLong: `The window may span at most ${String(MAX_WINDOW_DAYS)} whole UTC days.`,
  future: "The window may not end after today (UTC).",
} as const;

/** `from` is the first day's midnight, `to` the midnight after the last day (UTC). */
export interface ReportWindow {
  from: string;
  to: string;
}

export interface ClampedWindow extends ReportWindow {
  /** Why the server would refuse the window; null when it is valid. */
  error: string | null;
}

/** The URL's window: the first and last day, inclusive. */
export interface WindowSearch {
  first?: string;
  last?: string;
}

/** Midnight UTC of the day holding `ms`. */
function floorToDay(ms: number): number {
  return Math.floor(ms / DAY_MS) * DAY_MS;
}

/** "2026-10-01T00:00:00Z": the instant form the API and the URL use. */
function isoMidnight(ms: number): string {
  return `${new Date(ms).toISOString().slice(0, 10)}T00:00:00Z`;
}

function dayString(ms: number): string {
  return new Date(ms).toISOString().slice(0, 10);
}

/**
 * Widens `from` down and `to` up to whole UTC days, then checks the window as the server does:
 * at most 31 days, ending no later than the midnight after today. An invalid window keeps its
 * widened bounds and carries the reason.
 */
export function clampWindow(from: string, to: string, now: Date = new Date()): ClampedWindow {
  const start = Date.parse(from);
  const end = Date.parse(to);
  if (Number.isNaN(start) || Number.isNaN(end)) {
    return { from, to, error: WINDOW_ERRORS.invalid };
  }
  const first = floorToDay(start);
  const endDay = floorToDay(end);
  const after = endDay === end ? endDay : endDay + DAY_MS;
  const widened = { from: isoMidnight(first), to: isoMidnight(after) };
  if (after <= first) {
    return { ...widened, error: WINDOW_ERRORS.order };
  }
  if ((after - first) / DAY_MS > MAX_WINDOW_DAYS) {
    return { ...widened, error: WINDOW_ERRORS.tooLong };
  }
  if (after > floorToDay(now.getTime()) + DAY_MS) {
    return { ...widened, error: WINDOW_ERRORS.future };
  }
  return { ...widened, error: null };
}

/** The window of an inclusive first and last day (YYYY-MM-DD), as the date fields give them. */
export function windowFromDays(first: string, last: string, now: Date = new Date()): ClampedWindow {
  if (!DAY_PATTERN.test(first) || !DAY_PATTERN.test(last)) {
    return { from: first, to: last, error: WINDOW_ERRORS.invalid };
  }
  const after = Date.parse(`${last}T00:00:00Z`) + DAY_MS;
  return clampWindow(`${first}T00:00:00Z`, Number.isNaN(after) ? last : isoMidnight(after), now);
}

/** The server's default: the last 14 whole days before today. */
export function defaultWindow(now: Date = new Date()): ReportWindow {
  const today = floorToDay(now.getTime());
  return { from: isoMidnight(today - DEFAULT_WINDOW_DAYS * DAY_MS), to: isoMidnight(today) };
}

/** The URL's window when it is valid, else the default (a stale or hand-edited link). */
export function windowFromSearch(search: WindowSearch, now: Date = new Date()): ReportWindow {
  if (search.first === undefined || search.last === undefined) {
    return defaultWindow(now);
  }
  const reportWindow = windowFromDays(search.first, search.last, now);
  return reportWindow.error === null
    ? { from: reportWindow.from, to: reportWindow.to }
    : defaultWindow(now);
}

/** The inclusive first and last day and how many days the window holds. */
export function windowDays(reportWindow: ReportWindow): {
  first: string;
  last: string;
  count: number;
} {
  const start = Date.parse(reportWindow.from);
  const end = Date.parse(reportWindow.to);
  return {
    first: dayString(start),
    last: dayString(end - DAY_MS),
    count: Math.round((end - start) / DAY_MS),
  };
}

/** Today's UTC day, the latest a window may end on. */
export function todayUtc(now: Date = new Date()): string {
  return dayString(now.getTime());
}

const SHORT_DAY = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  timeZone: "UTC",
});

/** "Oct 7" for a YYYY-MM-DD day. */
export function shortDay(day: string): string {
  return SHORT_DAY.format(new Date(`${day}T00:00:00Z`));
}

function daysCount(count: number): string {
  return `${String(count)} ${count === 1 ? "day" : "days"}`;
}

/** "Oct 1 – Oct 14 · 14 days"; a single day is "Oct 14 · 1 day". */
export function windowLabel(reportWindow: ReportWindow): string {
  const { first, last, count } = windowDays(reportWindow);
  const range = first === last ? shortDay(first) : `${shortDay(first)} – ${shortDay(last)}`;
  return `${range} · ${daysCount(count)}`;
}

/** The picker's accessible name (Figma annotation): "Window, Oct 1 to Oct 14, 14 days, UTC". */
export function windowAccessibleName(reportWindow: ReportWindow): string {
  const { first, last, count } = windowDays(reportWindow);
  const range = first === last ? shortDay(first) : `${shortDay(first)} to ${shortDay(last)}`;
  return `Window, ${range}, ${daysCount(count)}, UTC`;
}
