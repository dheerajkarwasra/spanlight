import type { AlertEvent, AlertRule, AlertRuleFilters } from "@/lib/api";
import { MAX_WINDOW_MS } from "@/lib/time-range";

/**
 * A rule's filters as stored: people's rules use the typed keys; a budget's rule may also hold
 * its scope (`app.alerts.rules.metric_filters`).
 */
type StoredFilters = AlertRuleFilters & {
  external_user_id?: string;
  trace_name?: string;
  metadata_key?: string;
  metadata_value?: string;
  source_key_id?: string;
};

export type TracesRuleView = Pick<AlertRule, "metric" | "window_minutes"> & {
  filters: StoredFilters;
};

export interface EventLink {
  to: "/$orgId/$projectId/traces" | "/$orgId/$projectId/budgets";
  search: Record<string, string>;
  /** Filters the trace list can't express, as sentences: the link shows more than the rule. */
  unsupported: string[];
}

/** Rule filter → trace list search param. */
const TRACE_PARAMS = {
  model: "model",
  environment: "env",
  error_class: "error_class",
  external_user_id: "user",
  trace_name: "name",
  metadata_key: "metadata_key",
  metadata_value: "metadata_value",
} as const;

const UNSUPPORTED = {
  provider: "Traces can't filter by provider; showing all providers.",
  rate_limit_kind: "Traces can't filter by rate-limit kind; showing every failed call.",
  source_key_id: "Traces can't filter by gateway key; showing all keys.",
} as const;

/**
 * Where one firing leads: the traces the rule measured, from one window before it fired until it
 * resolved (or now), with the rule's filters; budget events lead to the budgets page. Null for a
 * rule that isn't loaded, whose window and filters are unknown.
 */
export function eventTracesLink(
  event: AlertEvent,
  rule: TracesRuleView | undefined,
  now: Date = new Date(),
): EventLink | null {
  if (event.rule_kind === "budget") {
    return { to: "/$orgId/$projectId/budgets", search: {}, unsupported: [] };
  }
  if (rule === undefined) {
    return null;
  }
  const to = event.resolved_at === null ? now : new Date(event.resolved_at);
  const start = Date.parse(event.started_at) - rule.window_minutes * 60_000;
  // The trace list refuses longer windows; a months-long firing shows its last 90 days.
  const from = new Date(Math.max(start, to.getTime() - MAX_WINDOW_MS));
  const search: Record<string, string> = {
    range: "custom",
    from: from.toISOString(),
    to: to.toISOString(),
  };
  for (const [filter, param] of Object.entries(TRACE_PARAMS)) {
    const value = rule.filters[filter as keyof typeof TRACE_PARAMS];
    if (value) {
      search[param] = value;
    }
  }
  if (rule.metric === "error_rate" || rule.metric === "error_calls") {
    search.status = "error";
  }
  const unsupported = Object.entries(UNSUPPORTED)
    .filter(([filter]) => rule.filters[filter as keyof typeof UNSUPPORTED])
    .map(([, sentence]) => sentence);
  return { to: "/$orgId/$projectId/traces", search, unsupported };
}
