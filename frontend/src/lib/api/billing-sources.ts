import { api, buildUrl } from "./client";
import { orgPath } from "./paths";
import type { Page, User } from "./types";

/**
 * Billing sources (spec §8.2): the provider accounts an organization reconciles its spend
 * against. The admin key is write-only: it goes out in a create or rotate body and no response
 * carries it (`key_set` and `key_id` say whether one is sealed).
 */

export type BillingSourceKind = "anthropic_admin_api" | "openai_costs_api" | "csv_upload";
export type BillingProvider = "anthropic" | "openai" | "google";
export type BillingSourceStatus = "never_fetched" | "ok" | "failing";
export type FetchTrigger = "scheduled" | "manual" | "csv_upload";
export type FetchRunStatus = "running" | "ok" | "failed";

export interface BillingSourceProject {
  id: string;
  name: string;
  slug: string;
}

export interface BillingSource {
  id: string;
  kind: BillingSourceKind;
  provider: BillingProvider;
  name: string;
  /** Anthropic workspace id or OpenAI project id the report is limited to; null for all. */
  provider_scope: string | null;
  enabled: boolean;
  status: BillingSourceStatus;
  key_set: boolean;
  key_id: string | null;
  last_fetched_at: string | null;
  last_success_at: string | null;
  /** An HTTP status line or a fixed short phrase, never a provider's response body. */
  last_error: string | null;
  projects: BillingSourceProject[];
  created_by: User | null;
  created_at: string;
  updated_at: string;
}

export interface BillingSourceCreate {
  kind: BillingSourceKind;
  name: string;
  /** Required for `csv_upload`; derived from the kind otherwise. */
  provider?: BillingProvider;
  /** Required for the API kinds, refused for `csv_upload`. */
  admin_key?: string;
  provider_scope?: string | null;
  project_ids: string[];
}

/** A partial edit; `project_ids` replaces the whole mapping, `provider_scope: null` clears it. */
export interface BillingSourceUpdate {
  name?: string;
  provider_scope?: string | null;
  enabled?: boolean;
  project_ids?: string[];
}

/** One fetch from the provider, or one CSV upload. */
export interface FetchRun {
  id: string;
  trigger: FetchTrigger;
  day_from: string;
  day_to: string;
  status: FetchRunStatus;
  error: string | null;
  lines_upserted: number;
  pages: number;
  requested_by: User | null;
  started_at: string;
  finished_at: string | null;
}

export interface RefreshAccepted {
  run_id: string;
}

export interface CsvUploadResult {
  run_id: string;
  lines_upserted: number;
  /** The UTC days (YYYY-MM-DD) the uploaded lines cover. */
  days: string[];
}

/** Why part of a gap between the bill and Spanlight's figures is known (spec §7.5). */
export type ExplainedKind =
  | "batch_spend"
  | "server_tool_fees"
  | "unpriced_spans"
  | "usage_not_reported"
  | "untraced_traffic"
  | "priority_commitment"
  | "unmodelled_modifiers"
  | "beyond_retention";

/** Tokens by billing class; every count is null while that side is unknown. */
export interface ReconciliationTokens {
  uncached_input: number | null;
  cache_read: number | null;
  cache_write_5m: number | null;
  cache_write_1h: number | null;
  output: number | null;
}

/** One known cause of a gap. `usd` is null when it has no computable amount (counted only). */
export interface ExplainedLine {
  kind: ExplainedKind;
  /** Signed: a priority commitment is negative. */
  usd: string | null;
  count: number | null;
  tokens: number | null;
  note: string;
}

/** One model group of one day: a seed or override pattern, or `unmapped:<name>`. */
export interface ReconciliationGroup {
  group: string;
  model_pattern: string | null;
  provider_models: string[];
  spanlight_models: string[];
  provider_usd: string | null;
  spanlight_usd: string | null;
  explained_usd: string | null;
  residual_usd: string | null;
  variance_ratio: string | null;
  provider_tokens: ReconciliationTokens;
  spanlight_tokens: ReconciliationTokens;
  explained: ExplainedLine[];
  /** "no provider line: …" when Spanlight has calls the provider billed nothing for. */
  note: string | null;
}

/** A credit, or a provider charge Spanlight cannot see: listed apart, never compared. */
export interface ListedCharge {
  description: string;
  usd: string;
}

/**
 * One UTC day. A day no successful fetch covers has `fetched_at`, the provider figures and the
 * residual null; a day whose rollups are being built has the Spanlight figures null and `note`
 * "rollup pending". `incomplete` also marks a day whose spans are past retention.
 */
export interface ReconciliationDay {
  /** YYYY-MM-DD (UTC). */
  day: string;
  provisional: boolean;
  fetched_at: string | null;
  incomplete: boolean;
  note: string | null;
  provider_usd: string | null;
  spanlight_usd: string | null;
  explained_usd: string | null;
  residual_usd: string | null;
  variance_ratio: string | null;
  credits_usd: string | null;
  other_usd: string | null;
  /** Calls whose span named no provider, so no source can bill them. */
  unattributed_calls: number;
  groups: ReconciliationGroup[];
  credits: ListedCharge[];
  other: ListedCharge[];
}

export interface ReconciliationSummary {
  /** The days the report shows. */
  days: number;
  settled_days: number;
  days_within_2pct: number;
  max_abs_variance_ratio: string | null;
}

/** Spec §8.3: the provider's bill against Spanlight's figures, per UTC day and model group. */
export interface ReconciliationReport {
  source: { id: string; provider: BillingProvider; kind: BillingSourceKind };
  /** Whole UTC days: `start` is the first day's midnight, `end` the midnight after the last. */
  window: { start: string; end: string };
  projects: { id: string; name: string }[];
  days: ReconciliationDay[];
  summary: ReconciliationSummary;
}

/** Widened to whole UTC days by the server; at most 31 days, ending today at the latest. */
export interface ReconciliationQuery {
  from?: string;
  to?: string;
}

function sourcePath(orgId: string, sourceId: string): string {
  return `${orgPath(orgId)}/billing-sources/${encodeURIComponent(sourceId)}`;
}

/** Reads need `org:read`; every write needs `billing_sources:manage` (admins and owners). */
export const billingSourcesApi = {
  list: (orgId: string): Promise<BillingSource[]> =>
    api.get<BillingSource[]>(`${orgPath(orgId)}/billing-sources`),
  get: (orgId: string, sourceId: string): Promise<BillingSource> =>
    api.get<BillingSource>(sourcePath(orgId, sourceId)),
  /**
   * `201` with the key check's outcome in `status`/`last_error`. `409 NOT_CONFIGURED` for an API
   * kind without CREDENTIALS_KEYS, `409 BILLING_SOURCE_NAME_TAKEN`, `409
   * BILLING_SOURCE_PROJECT_TAKEN` (the detail names the other source).
   */
  create: (orgId: string, input: BillingSourceCreate): Promise<BillingSource> =>
    api.post<BillingSource>(`${orgPath(orgId)}/billing-sources`, input),
  update: (orgId: string, sourceId: string, input: BillingSourceUpdate): Promise<BillingSource> =>
    api.patch<BillingSource>(sourcePath(orgId, sourceId), input),
  /** API kinds only (`422` on `kind` for a CSV source). */
  rotate: (orgId: string, sourceId: string, adminKey: string): Promise<BillingSource> =>
    api.post<BillingSource>(`${sourcePath(orgId, sourceId)}/rotate`, { admin_key: adminKey }),
  delete: (orgId: string, sourceId: string): Promise<void> =>
    api.delete(sourcePath(orgId, sourceId)),
  /**
   * `202` once a fetch is queued. `429 RATE_LIMITED` with `Retry-After`, `409
   * BILLING_SOURCE_DISABLED`, `409 BILLING_FETCH_IN_PROGRESS`, `409 NOT_CONFIGURED`.
   */
  refresh: (orgId: string, sourceId: string): Promise<RefreshAccepted> =>
    api.post<RefreshAccepted>(`${sourcePath(orgId, sourceId)}/refresh`),
  /**
   * CSV sources only. `422 VALIDATION_ERROR` with up to 20 `errors[]` of `{field: "file", row,
   * column, message}` (row 0 is the file as a whole) when nothing was stored; `413` past 5 MB.
   */
  upload: (orgId: string, sourceId: string, file: File): Promise<CsvUploadResult> => {
    const form = new FormData();
    form.append("file", file);
    return api.upload<CsvUploadResult>(`${sourcePath(orgId, sourceId)}/upload`, form);
  },
  /**
   * The reconciliation report over whole UTC days (the last 14 by default). `422
   * VALIDATION_ERROR` on `to` for a window longer than 31 days or ending after today (UTC).
   */
  reconciliation: (
    orgId: string,
    sourceId: string,
    query: ReconciliationQuery = {},
  ): Promise<ReconciliationReport> =>
    api.get<ReconciliationReport>(`${sourcePath(orgId, sourceId)}/reconciliation`, { ...query }),
  /**
   * The same report as a CSV file (one row per day and model group), as a URL for a plain download
   * link: the session cookie authenticates a GET, and the server names the file.
   */
  reconciliationCsvUrl: (
    orgId: string,
    sourceId: string,
    query: ReconciliationQuery = {},
  ): string => buildUrl(`${sourcePath(orgId, sourceId)}/reconciliation.csv`, { ...query }),
  /** Newest first; keyset paginated by `cursor`. */
  runs: (
    orgId: string,
    sourceId: string,
    query: { limit?: number; cursor?: string } = {},
  ): Promise<Page<FetchRun>> =>
    api.get<Page<FetchRun>>(`${sourcePath(orgId, sourceId)}/runs`, query),
};
