/**
 * Clock times and day names in the viewer's local time zone. Pure functions only; shared by the
 * session view and the freshness line.
 */

const clockFormat = new Intl.DateTimeFormat("en-US", {
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

const clockWithSecondsFormat = new Intl.DateTimeFormat("en-US", {
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hourCycle: "h23",
});

const monthDayFormat = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric" });

const monthDayYearFormat = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  year: "numeric",
});

/** The date of an ISO timestamp; null when it isn't one. */
export function parseDate(iso: string): Date | null {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? null : date;
}

/** Same calendar day in the viewer's time zone. */
export function isSameDay(a: Date, b: Date): boolean {
  return (
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  );
}

/** "Today", "Yesterday", "Oct 6", or "Oct 6, 2025" outside the current year. */
export function dayLabel(date: Date, now: Date = new Date()): string {
  if (isSameDay(date, now)) {
    return "Today";
  }
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (isSameDay(date, yesterday)) {
    return "Yesterday";
  }
  return date.getFullYear() === now.getFullYear()
    ? monthDayFormat.format(date)
    : monthDayYearFormat.format(date);
}

/** "09:12", or "09:12:04" with seconds. Null for an invalid timestamp. */
export function formatClock(iso: string, options: { seconds?: boolean } = {}): string | null {
  const date = parseDate(iso);
  if (date === null) {
    return null;
  }
  return (options.seconds ? clockWithSecondsFormat : clockFormat).format(date);
}

/** "Today 09:12:04", "Oct 6 22:14". Null for an invalid timestamp. */
export function formatDayTime(
  iso: string,
  now: Date = new Date(),
  options: { seconds?: boolean } = {},
): string | null {
  const date = parseDate(iso);
  const clock = formatClock(iso, options);
  if (date === null || clock === null) {
    return null;
  }
  return `${dayLabel(date, now)} ${clock}`;
}
