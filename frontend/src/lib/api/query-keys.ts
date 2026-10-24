import type { AlertEventQuery, AlertRulePreviewInput } from "./alerts-types";
import type { QueueItemListQuery } from "./annotations";
import type { AttributionQuery, AttributionSeriesQuery } from "./attribution-types";
import type { DatasetListQuery, ItemListQuery } from "./datasets";
import type { EvaluatorRunListQuery } from "./evaluators";
import type { PromptListQuery } from "./prompts";
import type { ReplayListQuery } from "./replays";
import type { AuditFilters } from "./orgs";
import type {
  MetricsQuery,
  SessionListQuery,
  SessionTurnsQuery,
  TimeWindow,
  TraceHistogramQuery,
  TraceListQuery,
} from "./projects";
import type {
  Bucket,
  CompletenessQuery,
  ErrorSamplesQuery,
  ErrorTimeseriesQuery,
  HealthQuery,
  IncidentListQuery,
  InsightListQuery,
  PrecisionQuery,
  ProviderHealthQuery,
  ReleaseCompareQuery,
  ReleaseListQuery,
  UserDetailQuery,
  UserListQuery,
} from "./types";

/**
 * Query key factory. Every key starts with a stable scope so related queries
 * can be invalidated together, e.g. `queryKeys.project(id).all`.
 */
export const queryKeys = {
  me: ["me"] as const,
  authSessions: ["auth", "sessions"] as const,
  authTokens: ["auth", "tokens"] as const,
  totp: ["auth", "totp"] as const,
  oauthProviders: ["auth", "oauth", "providers"] as const,
  oauthIdentities: ["auth", "oauth", "identities"] as const,
  prices: ["prices"] as const,
  invitePreview: (token: string) => ["invite", "preview", token] as const,

  org: (orgId: string) => {
    const all = ["org", orgId] as const;
    const audit = [...all, "audit"] as const;
    return {
      all,
      detail: [...all, "detail"] as const,
      projects: [...all, "projects"] as const,
      members: [...all, "members"] as const,
      invites: [...all, "invites"] as const,
      /** Every audit query of the org: invalidate with this, read one with `auditEvents`. */
      audit,
      auditEvents: (filters: AuditFilters) => [...audit, filters] as const,
      auditIntegrity: [...audit, "integrity"] as const,
      auditStream: [...all, "audit-stream"] as const,
      credentials: [...all, "credentials"] as const,
      priceOverrides: [...all, "price-overrides"] as const,
      /** The org's billing sources; one source and its runs sit under it, so this covers them. */
      billingSources: [...all, "billing-sources"] as const,
      billingSource: (sourceId: string) => [...all, "billing-sources", sourceId] as const,
      billingSourceRuns: (sourceId: string) =>
        [...all, "billing-sources", sourceId, "runs"] as const,
      alertChannels: [...all, "alert-channels"] as const,
      /** The delivery log of one channel; filters ride in the key. */
      deliveries: (channelId: string, filters: { status?: string | undefined } = {}) =>
        [...all, "alert-channels", channelId, "deliveries", filters] as const,
    };
  },

  project: (projectId: string) => {
    const all = ["project", projectId] as const;
    const gateway = [...all, "gateway"] as const;
    const doctor = [...all, "doctor"] as const;
    const providers = [...all, "providers"] as const;
    const prompts = [...all, "prompts"] as const;
    const datasets = [...all, "datasets"] as const;
    const annotations = [...all, "annotation-queues"] as const;
    const evaluators = [...all, "evaluators"] as const;
    const review = [...all, "review-policy"] as const;
    const replays = [...all, "replays"] as const;
    return {
      all,
      detail: [...all, "detail"] as const,
      keys: [...all, "keys"] as const,
      exports: [...all, "exports"] as const,
      export: (exportId: string) => [...all, "export", exportId] as const,
      /** Every unpriced-models query of the project: invalidate with this. */
      unpricedModelsAll: [...all, "unpriced-models"] as const,
      unpricedModels: (window: TimeWindow, environment: string | undefined) =>
        [...all, "unpriced-models", { ...window, environment }] as const,
      onboarding: [...all, "onboarding"] as const,
      filters: [...all, "filters"] as const,
      traces: (query: Omit<TraceListQuery, "cursor">) => [...all, "traces", query] as const,
      traceHistogram: (query: TraceHistogramQuery) => [...all, "trace-histogram", query] as const,
      trace: (traceId: string) => [...all, "trace", traceId] as const,
      /** One span with its payloads; under `trace(traceId)` so invalidating a trace covers it. */
      span: (traceId: string, spanId: string) =>
        [...all, "trace", traceId, "span", spanId] as const,
      traceComparison: (a: string, b: string) => [...all, "trace-comparison", a, b] as const,
      /** One span's cost explanation; under `trace(traceId)` so invalidating a trace covers it. */
      costExplanation: (traceId: string, spanId: string) =>
        [...all, "trace", traceId, "span", spanId, "cost-explanation"] as const,
      sessions: (query: Omit<SessionListQuery, "cursor">) => [...all, "sessions", query] as const,
      sessionTurns: (sessionId: string, query: Omit<SessionTurnsQuery, "cursor">) =>
        [...all, "session-turns", sessionId, query] as const,
      overview: (query: MetricsQuery) => [...all, "overview", query] as const,
      timeseries: (query: MetricsQuery & { bucket: Bucket }) =>
        [...all, "timeseries", query] as const,
      models: (query: MetricsQuery) => [...all, "models", query] as const,
      tokenMix: (window: TimeWindow) => [...all, "token-mix", window] as const,
      alertRules: [...all, "alert-rules"] as const,
      alertRule: (ruleId: string) => [...all, "alert-rules", ruleId] as const,
      /** Every rule's event timeline: invalidate with this. */
      alertEventsAll: [...all, "alert-events"] as const,
      alertEvents: (query: Omit<AlertEventQuery, "cursor">) =>
        [...all, "alert-events", query] as const,
      alertPreview: (spec: AlertRulePreviewInput) => [...all, "alert-preview", spec] as const,
      budgets: [...all, "budgets"] as const,
      /** Under `budgets`, so a budget edit invalidates its burn line too. */
      budgetBurn: (budgetId: string) => [...all, "budgets", budgetId, "burn"] as const,
      /** Every Doctor query of the project (insights, summary, health, runs): invalidate with this. */
      doctor,
      insights: (query: Omit<InsightListQuery, "cursor">) =>
        [...doctor, "insights", query] as const,
      insight: (insightId: string) => [...doctor, "insight", insightId] as const,
      insightsSummary: [...doctor, "summary"] as const,
      health: (query: HealthQuery) => [...doctor, "health", query] as const,
      detectorRuns: [...doctor, "detector-runs"] as const,
      precision: (query: PrecisionQuery) => [...doctor, "precision", query] as const,
      /** Every provider health query of the project: invalidate with this. */
      providers,
      providerHealth: (query: ProviderHealthQuery) => [...providers, "health", query] as const,
      providerErrors: (query: ErrorTimeseriesQuery) => [...providers, "errors", query] as const,
      providerSamples: (query: Omit<ErrorSamplesQuery, "cursor">) =>
        [...providers, "samples", query] as const,
      providerIncidents: (query: Omit<IncidentListQuery, "cursor">) =>
        [...providers, "incidents", query] as const,
      completeness: (query: CompletenessQuery) => [...all, "completeness", query] as const,
      releases: (query: ReleaseListQuery) => [...all, "releases", query] as const,
      releaseCompare: (query: ReleaseCompareQuery) => [...all, "release-compare", query] as const,
      /** Under the `releases` scope, so invalidating the release list covers it. */
      releaseFailureCategories: (query: ReleaseListQuery) =>
        [...all, "releases", "failure-categories", query] as const,
      users: (query: Omit<UserListQuery, "cursor">) => [...all, "users", query] as const,
      user: (userId: string, query: UserDetailQuery) => [...all, "user", userId, query] as const,
      attribution: (query: Omit<AttributionQuery, "cursor">) =>
        [...all, "attribution", query] as const,
      attributionSeries: (query: AttributionSeriesQuery) =>
        [...all, "attribution-series", query] as const,
      gateway: {
        /** Every gateway query of the project: invalidate with this. */
        all: gateway,
        overview: (query: MetricsQuery) => [...gateway, "overview", query] as const,
        keys: [...gateway, "keys"] as const,
        routes: [...gateway, "routes"] as const,
        route: (routeId: string) => [...gateway, "route", routeId] as const,
        versions: (routeId: string) => [...gateway, "route", routeId, "versions"] as const,
        faultProfiles: [...gateway, "fault-profiles"] as const,
      },
      prompts: {
        /** Every prompt query of the project: invalidate with this. */
        all: prompts,
        list: (query: Omit<PromptListQuery, "cursor">) => [...prompts, "list", query] as const,
        /** One prompt with its labels; `name` is the real name, slashes included. */
        detail: (name: string) => [...prompts, "prompt", name] as const,
        versions: (name: string) => [...prompts, "prompt", name, "versions"] as const,
        version: (name: string, version: number) =>
          [...prompts, "prompt", name, "version", version] as const,
        usage: (name: string, version: number) =>
          [...prompts, "prompt", name, "version", version, "usage"] as const,
        /** The plain first page the pickers read; the limit rides in the key. */
        picker: (limit: number) => [...prompts, "picker", limit] as const,
        /**
         * A prompt's versions as one plain page, for pickers. Under `versions(name)` so a new
         * version refreshes it, but never the same entry as the paged list of the prompt screen.
         */
        versionsPicker: (name: string, limit: number) =>
          [...prompts, "prompt", name, "versions", "picker", limit] as const,
        /** One version looked up by its id (`/prompt-versions/{id}`). */
        versionById: (versionId: string) => [...prompts, "version-by-id", versionId] as const,
      },
      scoreConfigs: [...all, "score-configs"] as const,
      /** The scores of one trace. */
      traceScores: (traceId: string) => [...all, "trace", traceId, "scores"] as const,
      datasets: {
        all: datasets,
        list: (query: Omit<DatasetListQuery, "cursor">) => [...datasets, "list", query] as const,
        detail: (datasetId: string) => [...datasets, "dataset", datasetId] as const,
        items: (datasetId: string, query: Omit<ItemListQuery, "cursor"> = {}) =>
          [...datasets, "dataset", datasetId, "items", query] as const,
      },
      annotations: {
        all: annotations,
        list: [...annotations, "list"] as const,
        detail: (queueId: string) => [...annotations, "queue", queueId] as const,
        items: (queueId: string, query: Omit<QueueItemListQuery, "cursor"> = {}) =>
          [...annotations, "queue", queueId, "items", query] as const,
      },
      review: {
        /** The policy and its selection history: invalidate with this. */
        all: review,
        policy: [...review, "policy"] as const,
        selections: [...review, "selections"] as const,
      },
      replays: {
        /** Every replay query of the project (sources, lists, budget): invalidate after a run. */
        all: replays,
        source: (traceId: string, spanId: string) =>
          [...replays, "source", traceId, spanId] as const,
        list: (query: Omit<ReplayListQuery, "cursor"> = {}) => [...replays, "list", query] as const,
        budget: [...replays, "budget"] as const,
      },
      evaluators: {
        all: evaluators,
        list: [...evaluators, "list"] as const,
        detail: (evaluatorId: string) => [...evaluators, "evaluator", evaluatorId] as const,
        runs: (evaluatorId: string, query: Omit<EvaluatorRunListQuery, "cursor"> = {}) =>
          [...evaluators, "evaluator", evaluatorId, "runs", query] as const,
        budget: (evaluatorId: string) =>
          [...evaluators, "evaluator", evaluatorId, "budget"] as const,
      },
      experiments: {
        /** Every experiment query of the project: invalidate with this. */
        all: [...all, "experiments"] as const,
        list: [...all, "experiments", "list"] as const,
        detail: (experimentId: string) => [...all, "experiments", experimentId] as const,
        runs: (experimentId: string) => [...all, "experiments", experimentId, "runs"] as const,
        run: (experimentId: string, runId: string) =>
          [...all, "experiments", experimentId, "run", runId] as const,
        items: (experimentId: string, runId: string) =>
          [...all, "experiments", experimentId, "run", runId, "items"] as const,
        compare: (experimentId: string, runA: string, runB: string) =>
          [...all, "experiments", experimentId, "compare", runA, runB] as const,
      },
    };
  },
};
