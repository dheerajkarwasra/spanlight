import { api } from "./client";
import { toPathId } from "./end-users";
import { projectPath } from "./paths";
import type {
  ApiKey,
  Bucket,
  CreatedApiKey,
  ErrorClass,
  FilterOptions,
  KeyScope,
  ModelMetrics,
  OnboardingStatus,
  OverviewMetrics,
  Page,
  Project,
  ProjectUpdate,
  SessionPage,
  SessionSort,
  SessionTurn,
  Span,
  TimeseriesPoint,
  TraceComparison,
  TraceDetail,
  TraceHistogram,
  TraceSort,
  TraceStatusFilter,
  TraceSummary,
} from "./types";

/** Time window shared by every metrics/list query. ISO-8601 UTC strings. */
export interface TimeWindow {
  from: string;
  to: string;
}

/**
 * LLM tokens by price class over whole UTC days, all environments (`GET …/metrics/token-mix`).
 * Every figure is null when the project has no cost rollup in those days.
 */
export interface TokenMix {
  first_day: string;
  last_day: string;
  uncached_input_tokens: number | null;
  output_tokens: number | null;
  cache_read_tokens: number | null;
  cache_write_tokens: number | null;
  fee_usd: string | null;
  cost_usd: string | null;
}

/** Which setup checklist steps the project has done (`GET …/activation`), read on request. */
export interface Activation {
  first_trace: boolean;
  session: boolean;
  budget: boolean;
  evaluation: boolean;
}

export interface MetricsQuery extends TimeWindow {
  environment?: string | undefined;
}

export interface TraceListQuery extends TimeWindow {
  environment?: string | undefined;
  release?: string | undefined;
  model?: string | undefined;
  status?: TraceStatusFilter | undefined;
  error_class?: ErrorClass | undefined;
  user_id?: string | undefined;
  session_id?: string | undefined;
  tag?: string | undefined;
  q?: string | undefined;
  prompt_version_id?: string | undefined;
  /** Traces with a score of this name; narrow it with the range or value below. */
  score_name?: string | undefined;
  score_min?: number | undefined;
  score_max?: number | undefined;
  /** A categorical score's value. */
  score_value?: string | undefined;
  has_scores?: boolean | undefined;
  /** Only traces with at least one retried call. */
  has_retries?: boolean | undefined;
  /** Traces whose metadata has this key; with `metadata_value`, that value for it. */
  metadata_key?: string | undefined;
  metadata_value?: string | undefined;
  /** Traces with exactly this name. */
  name?: string | undefined;
  /** The server's default is `started_at` (newest first). */
  sort?: TraceSort | undefined;
  /** The server's default is `desc`. */
  order?: "asc" | "desc" | undefined;
  /** Fill `input_preview` / `output_preview` on each trace. */
  preview?: boolean | undefined;
  limit?: number | undefined;
  cursor?: string | null | undefined;
}

/** The histogram takes exactly the list's filters, without its order and paging. */
export type TraceHistogramQuery = Omit<
  TraceListQuery,
  "sort" | "order" | "preview" | "limit" | "cursor"
>;

export interface SessionListQuery extends TimeWindow {
  /** Counts only the traces of this environment; omitted for every environment. */
  environment?: string | undefined;
  /** A session id prefix (ignoring case) or an exact user id. */
  q?: string | undefined;
  /** The server's default is `last` (latest turn first). */
  sort?: SessionSort | undefined;
  order?: "asc" | "desc" | undefined;
  /** Fill `last_input_preview` on each session. */
  preview?: boolean | undefined;
  limit?: number;
  cursor?: string | null;
}

/** A session's turns, newest first like `GET /traces?session_id=`, for as long as retained. */
export interface SessionTurnsQuery {
  environment?: string | undefined;
  limit?: number;
  cursor?: string | null;
}

export interface CreateApiKeyInput {
  name: string;
  /** At least one. The server's default is `ingest:write` only. */
  scopes?: KeyScope[];
  /** An ISO time in the future, or null for a key that never expires. */
  expires_at?: string | null;
}

export const projectsApi = {
  get: (projectId: string): Promise<Project> => api.get<Project>(projectPath(projectId)),
  update: (projectId: string, update: ProjectUpdate): Promise<Project> =>
    api.patch<Project>(projectPath(projectId), update),
  /**
   * Admin or owner. `confirm` is the project's slug exactly as typed; a wrong one is `422
   * CONFIRMATION_MISMATCH`. Deletes its traces, spans, API keys and export files.
   */
  delete: (projectId: string, confirm: string): Promise<void> =>
    api.delete(projectPath(projectId), { confirm }),

  keys: (projectId: string): Promise<ApiKey[]> =>
    api.get<ApiKey[]>(`${projectPath(projectId)}/keys`),
  /** The answer carries the key's secret, once. */
  createKey: (projectId: string, input: CreateApiKeyInput): Promise<CreatedApiKey> =>
    api.post<CreatedApiKey>(`${projectPath(projectId)}/keys`, input),
  revokeKey: (projectId: string, keyId: string): Promise<void> =>
    api.delete(`${projectPath(projectId)}/keys/${encodeURIComponent(keyId)}`),

  onboarding: (projectId: string): Promise<OnboardingStatus> =>
    api.get<OnboardingStatus>(`${projectPath(projectId)}/onboarding`),
  activation: (projectId: string): Promise<Activation> =>
    api.get<Activation>(`${projectPath(projectId)}/activation`),

  filters: (projectId: string): Promise<FilterOptions> =>
    api.get<FilterOptions>(`${projectPath(projectId)}/filters`),

  traces: (projectId: string, query: TraceListQuery): Promise<Page<TraceSummary>> =>
    api.get<Page<TraceSummary>>(`${projectPath(projectId)}/traces`, { ...query }),
  traceHistogram: (projectId: string, query: TraceHistogramQuery): Promise<TraceHistogram> =>
    api.get<TraceHistogram>(`${projectPath(projectId)}/traces/histogram`, { ...query }),
  trace: (projectId: string, traceId: string): Promise<TraceDetail> =>
    api.get<TraceDetail>(`${projectPath(projectId)}/traces/${encodeURIComponent(traceId)}`),
  compareTraces: (projectId: string, a: string, b: string): Promise<TraceComparison> =>
    api.get<TraceComparison>(`${projectPath(projectId)}/traces/compare`, { a, b }),

  /** One span with its payloads, for a span trace detail returned without them. */
  span: (projectId: string, traceId: string, spanId: string): Promise<Span> =>
    api.get<Span>(
      `${projectPath(projectId)}/traces/${encodeURIComponent(traceId)}/spans/${encodeURIComponent(spanId)}`,
    ),

  sessions: (projectId: string, query: SessionListQuery): Promise<SessionPage> =>
    api.get<SessionPage>(`${projectPath(projectId)}/sessions`, { ...query }),
  /** The session id travels as one path segment, `~`-prefixed like user ids (`toPathId`). */
  sessionTurns: (
    projectId: string,
    sessionId: string,
    query: SessionTurnsQuery,
  ): Promise<Page<SessionTurn>> =>
    api.get<Page<SessionTurn>>(
      `${projectPath(projectId)}/sessions/${encodeURIComponent(toPathId(sessionId))}/turns`,
      { ...query },
    ),

  overview: (projectId: string, query: MetricsQuery): Promise<OverviewMetrics> =>
    api.get<OverviewMetrics>(`${projectPath(projectId)}/metrics/overview`, { ...query }),
  timeseries: (
    projectId: string,
    query: MetricsQuery & { bucket: Bucket },
  ): Promise<TimeseriesPoint[]> =>
    api.get<TimeseriesPoint[]>(`${projectPath(projectId)}/metrics/timeseries`, { ...query }),
  models: (projectId: string, query: MetricsQuery): Promise<ModelMetrics[]> =>
    api.get<ModelMetrics[]>(`${projectPath(projectId)}/metrics/models`, { ...query }),
  /** Ignores `environment`: the cost rollup it reads has none. */
  tokenMix: (projectId: string, window: TimeWindow): Promise<TokenMix> =>
    api.get<TokenMix>(`${projectPath(projectId)}/metrics/token-mix`, { ...window }),
};
