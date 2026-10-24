import type { ResolvedRange } from "@/lib/time-range";

/**
 * Pure helpers for the Overview hero: the greeting, the date line and the status sentence
 * that sums up the selected window.
 */

export type DayPart = "morning" | "afternoon" | "evening";

/** 05:00–11:59 morning, 12:00–17:59 afternoon, otherwise evening (in the viewer's time zone). */
export function dayPartFor(date: Date): DayPart {
  const hour = date.getHours();
  if (hour >= 5 && hour < 12) {
    return "morning";
  }
  if (hour >= 12 && hour < 18) {
    return "afternoon";
  }
  return "evening";
}

/** The first word of the account name, or null when the name is blank. */
export function firstNameOf(fullName: string | null | undefined): string | null {
  const first = fullName?.trim().split(/\s+/)[0];
  return first ? first : null;
}

/** "Good afternoon, Dheeraj." — or "Good afternoon." when there is no name to use. */
export function greetingFor(date: Date, fullName: string | null | undefined): string {
  const firstName = firstNameOf(fullName);
  const salutation = `Good ${dayPartFor(date)}`;
  return firstName ? `${salutation}, ${firstName}.` : `${salutation}.`;
}

const weekdayFormat = new Intl.DateTimeFormat("en-GB", { weekday: "long" });
const dayMonthFormat = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "long" });

/** "Thursday, 8 October" in the viewer's time zone. */
export function formatHeroDate(date: Date): string {
  return `${weekdayFormat.format(date)}, ${dayMonthFormat.format(date)}`;
}

/** The eyebrow parts joined with a middle dot, e.g. "Thursday, 8 October · production · Last 24 hours". */
export function joinEyebrow(parts: readonly (string | null | undefined)[]): string {
  return parts.filter((part): part is string => Boolean(part)).join(" · ");
}

const SHORT_RANGE_LABELS: Record<ResolvedRange["value"], string> = {
  "1h": "last hour",
  "24h": "last 24h",
  "7d": "last 7 days",
  "30d": "last 30 days",
  custom: "selected range",
};

/** Lower-case label for card headings, e.g. "Spend · last 24h". */
export function shortRangeLabel(range: Pick<ResolvedRange, "value">): string {
  return SHORT_RANGE_LABELS[range.value];
}

export type HeroStatusKind = "first-run" | "empty-window" | "errors" | "findings" | "healthy";

/** The status sentence: a plain lead followed by an emphasised (serif) accent. */
export interface HeroStatus {
  kind: HeroStatusKind;
  lead: string;
  accent: string;
}

interface HeroStatusInput {
  /** Traces in the selected window. */
  traces: number;
  /** Failed LLM calls in the selected window. */
  errors: number;
  /** Open Doctor findings (critical + warning); 0 while unknown. */
  findings?: number;
  /** False when the project has never received a trace; null while that is unknown. */
  hasEverReceivedTraces: boolean | null;
}

/**
 * Sums up the window in one sentence:
 * - nothing ever received → "Waiting for your first trace."
 * - nothing in this window (but older traces exist) → "No traces in this window."
 * - failed calls and open Doctor findings → "3 errors and 1 finding need a look."
 * - open findings only → "1 finding needs a look."
 * - otherwise → "Your models are behaving."
 */
export function heroStatus({
  traces,
  errors,
  findings = 0,
  hasEverReceivedTraces,
}: HeroStatusInput): HeroStatus {
  if (traces === 0) {
    if (hasEverReceivedTraces === false) {
      return { kind: "first-run", lead: "Waiting for your", accent: "first trace." };
    }
    return { kind: "empty-window", lead: "No traces in", accent: "this window." };
  }
  const parts = [countOf(errors, "error"), countOf(findings, "finding")].filter(Boolean);
  if (parts.length === 0) {
    return { kind: "healthy", lead: "Your models are", accent: "behaving." };
  }
  return {
    kind: errors > 0 ? "errors" : "findings",
    lead: parts.join(" and "),
    accent: errors + findings === 1 ? "needs a look." : "need a look.",
  };
}

/** "1 error", "1,203 errors"; null for zero. */
function countOf(count: number, noun: string): string | null {
  if (count <= 0) {
    return null;
  }
  return count === 1 ? `1 ${noun}` : `${new Intl.NumberFormat("en-US").format(count)} ${noun}s`;
}

/**
 * Failed LLM calls in the window. The API reports the rate (errors ÷ calls), so the count is
 * recovered from it; rounding removes floating-point noise. Null when the rate is unknown.
 */
export function errorCountFrom(errorRate: number | null, llmCalls: number): number | null {
  if (errorRate === null || !Number.isFinite(errorRate)) {
    return null;
  }
  return Math.round(errorRate * llmCalls);
}
