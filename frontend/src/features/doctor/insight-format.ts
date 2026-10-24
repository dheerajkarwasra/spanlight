import type {
  FailureLayer,
  Insight,
  InsightCertainty,
  InsightSeverity,
  InsightStatus,
} from "@/lib/api";
import { pluralize } from "@/lib/format";

/*
 * Copy and formatting for insights that the list and the detail page share. Insight titles,
 * summaries and fixes come from the API; everything here is the frame around them.
 */

export const SEVERITY_LABELS: Record<InsightSeverity, string> = {
  critical: "Critical",
  warning: "Warning",
  info: "Info",
};

export const SEVERITIES: readonly InsightSeverity[] = ["critical", "warning", "info"];

export const STATUS_LABELS: Record<InsightStatus, string> = {
  open: "Open",
  acknowledged: "Acknowledged",
  muted: "Muted",
  resolved: "Resolved",
};

/** The status tabs, in the order the spec lists them. */
export const STATUSES: readonly InsightStatus[] = ["open", "acknowledged", "muted", "resolved"];

/** The detector catalogue's names, for the kind filter and for kinds the server did not label. */
const KIND_LABELS: Record<string, string> = {
  error_spike: "Error spike",
  latency_regression: "Latency regression",
  cost_spike: "Cost spike",
  retry_storm: "Retry storm",
  retry_after_ignored: "Retry-After ignored",
  rate_limit_pressure: "Rate-limit pressure",
  truncated_outputs: "Truncated outputs",
  context_growth: "Context growth",
  cache_opportunity: "Prompt-cache opportunity",
  tool_loop: "Tool loop",
  truncated_stream_accepted: "Truncated stream accepted",
  unsupported_parameter_retried: "Rejected parameter dropped",
  client_timeout_misconfigured: "Client timeout too short",
  unpriced_spend: "Unpriced spend",
  provider_incident: "Provider incident",
};

export const INSIGHT_KINDS: readonly string[] = Object.keys(KIND_LABELS);

/** "retry_storm" → "Retry storm"; a kind the catalogue does not know is spelled out. */
export function insightKindLabel(kind: string): string {
  const known = KIND_LABELS[kind];
  if (known !== undefined) {
    return known;
  }
  const spaced = kind.replaceAll("_", " ").trim();
  return spaced === "" ? kind : spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

export const FAILURE_LAYERS: readonly FailureLayer[] = [
  "client",
  "request",
  "agent",
  "provider",
  "platform",
  "traffic",
];

export const LAYER_LABELS: Record<FailureLayer, string> = {
  client: "Client",
  request: "Request",
  agent: "Agent",
  provider: "Provider",
  platform: "Platform",
  traffic: "Traffic",
};

export const LAYER_DESCRIPTIONS: Record<FailureLayer, string> = {
  client: "How the app calls the model API.",
  request: "What the app sends: prompts, parameters, context.",
  agent: "Tool orchestration.",
  provider: "The model provider.",
  platform: "Spanlight setup, such as prices.",
  traffic: "An aggregate symptom whose cause is still open.",
};

export const CERTAINTY_COPY: Record<InsightCertainty, { label: string; note: string }> = {
  measured: {
    label: "Measured",
    note: "The finding restates numbers the Doctor observed.",
  },
  inferred: {
    label: "Inferred from a pattern; confirm in the traces",
    note: "A pattern that strongly suggests the cause; the traces can confirm it.",
  },
};

const MINUTE_MS = 60_000;
const HOUR_MS = 60 * MINUTE_MS;
const DAY_MS = 24 * HOUR_MS;

function parse(iso: string | null | undefined): Date | null {
  if (!iso) {
    return null;
  }
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? null : date;
}

/** "just now", "32 s ago", "14 min ago", "3 h ago", "2 d ago"; null for an unreadable time. */
export function compactRelative(iso: string | null | undefined, now: Date): string | null {
  const date = parse(iso);
  if (date === null) {
    return null;
  }
  const delta = Math.max(0, now.getTime() - date.getTime());
  if (delta < 5_000) {
    return "just now";
  }
  if (delta < MINUTE_MS) {
    return `${Math.floor(delta / 1000)} s ago`;
  }
  if (delta < HOUR_MS) {
    return `${Math.floor(delta / MINUTE_MS)} min ago`;
  }
  if (delta < DAY_MS) {
    return `${Math.floor(delta / HOUR_MS)} h ago`;
  }
  return `${Math.floor(delta / DAY_MS)} d ago`;
}

const monthDayClock = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});
const clockOnly = new Intl.DateTimeFormat("en-US", {
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

/** "Oct 10, 16:42" in the viewer's time zone, 24-hour; null for an unreadable time. */
export function formatDateTime(iso: string | null | undefined): string | null {
  const date = parse(iso);
  return date === null ? null : monthDayClock.format(date);
}

/** "15 min window", "3 h window": the evidence window's length; null for an unreadable time. */
export function windowLength(start: string, end: string): string | null {
  const from = parse(start);
  const to = parse(end);
  if (from === null || to === null) {
    return null;
  }
  const minutes = Math.max(1, Math.round((to.getTime() - from.getTime()) / MINUTE_MS));
  return minutes >= 120 && minutes % 60 === 0
    ? `${minutes / 60} h window`
    : `${minutes} min window`;
}

/** "Oct 10, 14:30 – 14:45 · 15 min window": the evidence window. */
export function formatWindow(start: string, end: string): string | null {
  const from = parse(start);
  const to = parse(end);
  const length = windowLength(start, end);
  if (from === null || to === null || length === null) {
    return null;
  }
  return `${monthDayClock.format(from)} – ${clockOnly.format(to)} · ${length}`;
}

/** "41 occurrences · last seen 3 min ago". */
export function activityLine(insight: Insight, now: Date): string {
  const seen = compactRelative(insight.last_seen_at, now);
  const count = pluralize(insight.occurrences, "occurrence");
  return seen === null ? count : `${count} · last seen ${seen}`;
}

/** The list's footer: "Showing 50 insights" while more pages exist, else "All 120 insights shown". */
export function loadedText(loaded: number, hasNextPage: boolean): string {
  const counted = pluralize(loaded, "insight");
  if (hasNextPage) return `Showing ${counted}`;
  return loaded === 1 ? `${counted} shown` : `All ${counted} shown`;
}

/** "8 open · 2 critical · 4 warning · 2 info" over the loaded insights; only non-zero parts. */
export function listSummary(
  items: readonly Insight[],
  status: InsightStatus,
  hasMore: boolean,
): string {
  const word = STATUS_LABELS[status].toLowerCase();
  const count = hasMore ? `${items.length}+ ${word}` : `${items.length} ${word}`;
  const parts = SEVERITIES.map((severity) => ({
    severity,
    count: items.filter((item) => item.severity === severity).length,
  }))
    .filter((part) => part.count > 0)
    .map((part) => `${part.count} ${part.severity}`);
  return [count, ...parts].join(" · ");
}

export interface TextPart {
  text: string;
  code: boolean;
}

/** Splits catalogue copy on `backticks`: the odd pieces are code. Never produces markup. */
export function splitInlineCode(text: string): TextPart[] {
  return text
    .split("`")
    .map((piece, index) => ({ text: piece, code: index % 2 === 1 }))
    .filter((part) => part.text !== "");
}

/**
 * The words for when the Doctor last checked, or why that is not known. `failed` is the request
 * for the newest run; `runFailed` is that run itself ending in an error.
 */
export function lastCheckedText(
  state: { pending: boolean; failed: boolean; runFailed: boolean; ranAt: string | null },
  now: Date,
): string {
  if (state.pending) {
    return "Last checked —";
  }
  if (state.failed) {
    return "Last check unknown";
  }
  const relative = compactRelative(state.ranAt, now);
  if (relative === null) {
    return "Not checked yet";
  }
  return state.runFailed ? `Last check failed ${relative}` : `Last checked ${relative}`;
}
