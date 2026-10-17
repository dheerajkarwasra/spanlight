import type { AlertRuleKind, Comparator } from "@/lib/api";

/** The rule editor's fixed copy and choices (Figma "Alerts — Rule editor"; spec §11.1). */
export const RULE_EDITOR_COPY = {
  createTitle: "New alert rule",
  description: "Checked every minute. Alerts go to the channels you pick.",
  nameHint: "Shown in alerts and in the rules list.",
  namePlaceholder: "Name this rule",
  kindHints: {
    threshold: "Fires when the metric crosses a fixed line.",
    anomaly: "Fires when the metric is unusual compared with its recent past.",
  } satisfies Record<AlertRuleKind, string>,
  baselineHint: "Previous windows used as the baseline, 3 to 30.",
  sensitivityHint: "Higher means fewer alerts. 3 is a good start.",
  cooldownHint:
    "After a rule resolves, it stays quiet this long even if the metric breaches again.",
  filtersHint: "Leave a filter on “Any” to include everything.",
  errorFiltersHint:
    "Error class and Rate-limit kind appear only for Error rate and Failed LLM calls. Leave a filter on “Any” to include everything.",
  channelsHint: "Up to 10 channels.",
  channelsPlaceholder: "Add a channel",
  noChannelsPlaceholder: "No channels yet",
  noChannelsHint: "Rules need somewhere to send alerts. Add a channel first.",
  enabled: "Rule enabled",
} as const;

export const KIND_TAB_LABELS: Record<AlertRuleKind, string> = {
  threshold: "Threshold",
  anomaly: "Anomaly",
};

export const COMPARATORS: readonly Comparator[] = ["gt", "gte", "lt", "lte"];

/** The "Condition" select of a threshold rule. */
export const CONDITION_OPTIONS: Record<Comparator, string> = {
  gt: "Is above (>)",
  gte: "Is at or above (≥)",
  lt: "Is below (<)",
  lte: "Is at or below (≤)",
};

/** The "Direction" select of an anomaly rule. */
export const DIRECTION_OPTIONS: Record<Comparator, string> = {
  gt: "Unusually high (>)",
  gte: "Unusually high (≥)",
  lt: "Unusually low (<)",
  lte: "Unusually low (≤)",
};

/** The windows offered; a stored rule with another length keeps it as an extra option. */
export const WINDOW_PRESETS: readonly number[] = [5, 10, 15, 30, 60, 120, 180, 360, 720, 1440];

/** "10 minutes", "60 minutes", "2 hours", "24 hours". */
export function windowOptionLabel(minutes: number): string {
  if (minutes > 60 && minutes % 60 === 0) {
    return `${minutes / 60} hours`;
  }
  return minutes === 1 ? "1 minute" : `${minutes} minutes`;
}

export function windowOptions(current: number): number[] {
  return WINDOW_PRESETS.includes(current)
    ? [...WINDOW_PRESETS]
    : [...WINDOW_PRESETS, current].sort((a, b) => a - b);
}

/** The error summary above the form after a refused save. */
export function errorSummary(count: number, editing: boolean): string {
  const fields = count === 1 ? "the highlighted field" : `the ${count} highlighted fields`;
  return `Fix ${fields} to ${editing ? "save this rule" : "create this rule"}.`;
}
