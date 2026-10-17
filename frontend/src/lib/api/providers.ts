import { api } from "./client";
import { projectPath } from "./paths";
import type {
  ErrorBucket,
  ErrorSamplesPage,
  ErrorSamplesQuery,
  ErrorTimeseriesQuery,
  HealthReport,
  IncidentList,
  IncidentListQuery,
  ProviderHealthQuery,
} from "./types";

function providersPath(projectId: string): string {
  return `${projectPath(projectId)}/providers`;
}

/**
 * Provider health and the providers' status page incidents. Every route needs `project:read`; a
 * window defaults to the last 24 hours. `422 VALIDATION_ERROR` for a bad window or an unknown
 * error class or rate-limit kind.
 */
export const providersApi = {
  /** LLM calls, failures, 429 kinds, retries and incidents per provider, busiest first. */
  health: (projectId: string, query: ProviderHealthQuery = {}): Promise<HealthReport> =>
    api.get<HealthReport>(`${providersPath(projectId)}/health`, { ...query }),
  /** Failed LLM calls per UTC bucket and class; classes with no failures are absent. */
  errorTimeseries: (projectId: string, query: ErrorTimeseriesQuery): Promise<ErrorBucket[]> =>
    api.get<ErrorBucket[]>(`${providersPath(projectId)}/errors/timeseries`, { ...query }),
  /** The newest failed calls; a window over 24 hours is cut to its last 24 (`clamped`). */
  samples: (projectId: string, query: ErrorSamplesQuery = {}): Promise<ErrorSamplesPage> =>
    api.get<ErrorSamplesPage>(`${providersPath(projectId)}/errors/samples`, { ...query }),
  /** Incidents overlapping the window, newest first, and every feed's poll state. */
  incidents: (projectId: string, query: IncidentListQuery = {}): Promise<IncidentList> =>
    api.get<IncidentList>(`${projectPath(projectId)}/provider-incidents`, { ...query }),
};
