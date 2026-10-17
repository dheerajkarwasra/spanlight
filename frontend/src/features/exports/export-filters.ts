/**
 * What a trace export asks for: the trace list's current filters as the export request carries
 * them, the 90-day window rule, and the short text that describes a set of filters. Pure
 * functions only, so the traces page and the exports list agree on every word.
 */
import type { ErrorClass, ExportFilters } from "@/lib/api";
import { MAX_WINDOW_MS } from "@/lib/time-range";

/** The limit the API enforces between `from` and `to`; the dialog checks it before any request. */
export const MAX_EXPORT_DAYS = MAX_WINDOW_MS / (24 * 3600_000);

const DAY_MS = 24 * 3600_000;

/** The trace list's facet filters, under the names its URL uses. */
export interface TraceFacets {
  release?: string | undefined;
  model?: string | undefined;
  status?: "ok" | "error" | undefined;
  error_class?: ErrorClass | undefined;
  tag?: string | undefined;
  user?: string | undefined;
  session?: string | undefined;
  metadata_key?: string | undefined;
  metadata_value?: string | undefined;
  q?: string | undefined;
}

export interface ExportWindow {
  from: string;
  to: string;
}

function present(value: string | undefined): string | undefined {
  const trimmed = value?.trim();
  return trimmed ? trimmed : undefined;
}

/**
 * The export `filters` for the trace list as it is on screen: the same window, environment and
 * facets the list query sends, under the API's names (`user` is `user_id`, `session` is
 * `session_id`). Filters that are not set are left out rather than sent empty. The metadata value
 * is sent as typed, like the list sends it, and only with its key: the key alone is the
 * missing-key filter.
 */
export function toExportFilters(
  window: ExportWindow,
  environment: string | undefined,
  facets: TraceFacets,
): ExportFilters {
  const filters: ExportFilters = { from: window.from, to: window.to };
  const environmentValue = present(environment);
  if (environmentValue) {
    filters.environment = environmentValue;
  }
  const release = present(facets.release);
  if (release) {
    filters.release = release;
  }
  const model = present(facets.model);
  if (model) {
    filters.model = model;
  }
  if (facets.status) {
    filters.status = facets.status;
  }
  if (facets.error_class) {
    filters.error_class = facets.error_class;
  }
  const userId = present(facets.user);
  if (userId) {
    filters.user_id = userId;
  }
  const sessionId = present(facets.session);
  if (sessionId) {
    filters.session_id = sessionId;
  }
  const tag = present(facets.tag);
  if (tag) {
    filters.tag = tag;
  }
  if (facets.metadata_key) {
    filters.metadata_key = facets.metadata_key;
    if (facets.metadata_value) {
      filters.metadata_value = facets.metadata_value;
    }
  }
  const q = present(facets.q);
  if (q) {
    filters.q = q;
  }
  return filters;
}

export interface ExportWindowProblem {
  kind: "too-long" | "invalid";
  /** Whole days the window spans, rounded up; 0 when the window is invalid. */
  days: number;
}

/** Why the window cannot be exported, or null when it can: longer than 90 days, or not a range. */
export function exportWindowProblem(window: ExportWindow): ExportWindowProblem | null {
  const span = Date.parse(window.to) - Date.parse(window.from);
  if (!Number.isFinite(span) || span <= 0) {
    return { kind: "invalid", days: 0 };
  }
  if (span > MAX_WINDOW_MS) {
    return { kind: "too-long", days: Math.ceil(span / DAY_MS) };
  }
  return null;
}

export interface FilterEntry {
  key: string;
  value: string;
}

/** The filters beyond the window that are set, in the API's names and a stable order. */
export function exportFilterEntries(filters: ExportFilters): FilterEntry[] {
  const candidates: [string, string | null | undefined][] = [
    ["environment", filters.environment],
    ["release", filters.release],
    ["model", filters.model],
    ["status", filters.status],
    ["error_class", filters.error_class],
    ["user_id", filters.user_id],
    ["session_id", filters.session_id],
    ["tag", filters.tag],
    ["metadata_key", filters.metadata_key],
    ["metadata_value", filters.metadata_value],
    ["q", filters.q],
  ];
  const entries: FilterEntry[] = [];
  for (const [key, value] of candidates) {
    if (value) {
      entries.push({ key, value });
    }
  }
  return entries;
}

/** "status: error · model: gpt-4.1", or "All traces" when only the window is set. */
export function exportFilterSummary(filters: ExportFilters): string {
  const entries = exportFilterEntries(filters);
  if (entries.length === 0) {
    return "All traces";
  }
  return entries.map((entry) => `${entry.key}: ${entry.value}`).join(" · ");
}
