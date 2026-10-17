import type { ReviewPolicy, ReviewPolicyInput, ReviewSelection } from "@/lib/api";
import { pluralize } from "@/lib/format";

const DAY_MS = 24 * 3600 * 1000;

const utcDayFormat = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  timeZone: "UTC",
});

/** Midnight UTC of the day `date` falls in, in ms. */
function utcMidnight(date: Date): number {
  return Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate());
}

/** Whole UTC days from today to `date`: -1 is yesterday, 1 is tomorrow. */
function daysFromToday(date: Date, now: Date): number {
  return Math.round((utcMidnight(date) - utcMidnight(now)) / DAY_MS);
}

const weekdayFormat = new Intl.DateTimeFormat("en-US", {
  weekday: "short",
  month: "short",
  day: "numeric",
  timeZone: "UTC",
});

/** "Yesterday · Thu, Oct 16" or "Wed, Oct 15" for a `YYYY-MM-DD` UTC day. */
export function dayOptionLabel(day: string, now: Date = new Date()): string {
  const date = new Date(`${day}T00:00:00Z`);
  const label = weekdayFormat.format(date);
  return daysFromToday(date, now) === -1 ? `Yesterday · ${label}` : label;
}

/** How each reason's queued count reads in the last-selection line. */
const REASON_COUNTS: [key: string, singular: string, plural: string][] = [
  ["doctor_finding", "finding", "findings"],
  ["error", "error", "errors"],
  ["cost_outlier", "outlier", "outliers"],
  ["random", "random", "random"],
];

function total(counts: Record<string, number>): number {
  return Object.values(counts).reduce((sum, count) => sum + count, 0);
}

/**
 * "Yesterday: 50 queued of 212 candidates · 12 findings · 20 errors · 6 outliers · 12 random · cap
 * reached". The day is relative to `now` (UTC); reasons with nothing queued are left out. Null
 * before the first selection.
 */
export function lastSelectionText(
  selection: ReviewSelection | null,
  now: Date = new Date(),
): string | null {
  if (selection === null) {
    return null;
  }
  const day = new Date(`${selection.day}T00:00:00Z`);
  const offset = daysFromToday(day, now);
  const label = offset === -1 ? "Yesterday" : offset === 0 ? "Today" : utcDayFormat.format(day);
  const parts = [
    `${String(total(selection.queued))} queued of ${pluralize(total(selection.candidates), "candidate")}`,
    ...REASON_COUNTS.flatMap(([key, singular, plural]) => {
      const count = selection.queued[key] ?? 0;
      return count > 0 ? [pluralize(count, singular, plural)] : [];
    }),
    ...(selection.cap_hit ? ["cap reached"] : []),
  ];
  return `${label}: ${parts.join(" · ")}`;
}

/** "Next selection: tomorrow 01:00 UTC", or "Paused" while the policy is disabled. */
export function nextRunText(
  policy: Pick<ReviewPolicy, "enabled" | "next_run_at">,
  now: Date = new Date(),
): string {
  if (!policy.enabled || policy.next_run_at === null) {
    return "Paused";
  }
  const at = new Date(policy.next_run_at);
  const offset = daysFromToday(at, now);
  const day = offset === 1 ? "tomorrow" : offset === 0 ? "today" : utcDayFormat.format(at);
  return `Next selection: ${day} ${at.toISOString().slice(11, 16)} UTC`;
}

/** "1%", "0.5%", "5%" from the policy's 4-place rate string. */
export function ratePercent(rate: string): string {
  return `${String(Number((Number(rate) * 100).toFixed(2)))}%`;
}

/**
 * The full `PUT` body of a policy with some fields changed. `PUT` resets an omitted field to its
 * default, so a toggle sends every field.
 */
export function policyInput(
  policy: ReviewPolicy,
  change: Partial<ReviewPolicyInput> = {},
): ReviewPolicyInput {
  return {
    queue_id: policy.queue_id,
    enabled: policy.enabled,
    random_rate: policy.random_rate,
    include_errors: policy.include_errors,
    include_cost_outliers: policy.include_cost_outliers,
    include_doctor_findings: policy.include_doctor_findings,
    daily_cap: policy.daily_cap,
    environments: policy.environments,
    ...change,
  };
}
