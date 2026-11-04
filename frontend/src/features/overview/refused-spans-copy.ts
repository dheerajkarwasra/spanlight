import type { RefusedDay } from "@/lib/api";
import { formatInteger } from "@/lib/format";

export type { RefusedDay };

/** The info icon's tooltip. "Refused", never "lost": the server knows what it refused, not what
 * the client did next. */
export const REFUSED_TOOLTIP =
  "Spans in requests the server refused. A client that retried a refused batch is counted once per refusal, and a span it resent successfully is also in the stored total.";

/** "1 Nov", in UTC: the days are UTC dates. */
const dayFormat = new Intl.DateTimeFormat("en-GB", {
  day: "numeric",
  month: "short",
  timeZone: "UTC",
});

function formatDay(day: string): string {
  return dayFormat.format(new Date(`${day}T00:00:00Z`));
}

const DAY_MS = 24 * 3600_000;

function consecutive(first: string, second: string): boolean {
  return Date.parse(`${second}T00:00:00Z`) - Date.parse(`${first}T00:00:00Z`) === DAY_MS;
}

/**
 * "on 1 Nov"; two days: "on 1–2 Nov" (or "on 31 Oct–1 Nov") when consecutive, else "on 1 and
 * 5 Nov"; three or more: "on 3 days". A range is only ever two adjacent days.
 */
function dayPhrase(days: readonly string[]): string {
  const [first, second] = days;
  if (first === undefined) {
    return "";
  }
  if (days.length === 1 || second === undefined) {
    return `on ${formatDay(first)}`;
  }
  if (days.length === 2) {
    const start = formatDay(first);
    const end = formatDay(second);
    const sameMonth = start.split(" ")[1] === end.split(" ")[1];
    const startDay = start.split(" ")[0] ?? start;
    if (consecutive(first, second)) {
      return sameMonth ? `on ${startDay}–${end}` : `on ${start}–${end}`;
    }
    return sameMonth ? `on ${startDay} and ${end}` : `on ${start} and ${end}`;
  }
  return `on ${String(days.length)} days`;
}

function count(n: number): string {
  return formatInteger(n) ?? String(n);
}

/**
 * The notice's sentences for a window: the daily-quota sentence (with its incompleteness warning),
 * then the rate-limit sentence (refused spans a client probably resent); empty when nothing was
 * refused. Each counts only its own refusals, so the two never share a span.
 */
export function refusedSentences(days: readonly RefusedDay[]): string[] {
  const daily = days.filter((d) => d.spans - d.rate_spans > 0);
  const rate = days.filter((d) => d.rate_spans > 0);
  const sentences: string[] = [];
  if (daily.length > 0) {
    const total = daily.reduce((sum, d) => sum + d.spans - d.rate_spans, 0);
    sentences.push(
      `${count(total)} spans were refused by this project's daily ingest quota ${dayPhrase(daily.map((d) => d.day))}. The totals for this range may be incomplete.`,
    );
  }
  if (rate.length > 0) {
    const total = rate.reduce((sum, d) => sum + d.rate_spans, 0);
    sentences.push(
      `${count(total)} spans were refused by this project's ingest rate limit ${dayPhrase(rate.map((d) => d.day))}; clients that retry may have sent them again.`,
    );
  }
  return sentences;
}
