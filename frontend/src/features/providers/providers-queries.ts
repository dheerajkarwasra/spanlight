import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { useProjectFilters, useProjectParams } from "@/features/shell/project-context";
import {
  providersApi,
  queryKeys,
  type ErrorClass,
  type ErrorSamplesQuery,
  type ErrorTimeseriesQuery,
  type IncidentListQuery,
  type ProviderHealthQuery,
  type RateLimitKind,
} from "@/lib/api";
import { bucketFor } from "@/lib/time-range";

/** The newest failed calls the page lists (the server's default and the spec's 20). */
export const SAMPLE_LIMIT = 20;

/** Incidents overlapping the window, newest first: the API's maximum page. */
const INCIDENT_LIMIT = 100;

/** The URL's window and environment as the provider routes take them. */
function useHealthWindow(): ProviderHealthQuery {
  const { range, environment } = useProjectFilters();
  return { from: range.from, to: range.to, environment };
}

/** Calls, failures, 429 kinds, retries and incidents per provider, busiest first. */
export function useProviderHealth() {
  const { projectId } = useProjectParams();
  const query = useHealthWindow();
  return useQuery({
    queryKey: queryKeys.project(projectId).providerHealth(query),
    queryFn: () => providersApi.health(projectId, query),
    placeholderData: keepPreviousData,
  });
}

/** Failed calls per bucket and class, for every provider or one. */
export function useErrorSeries(provider: string | undefined) {
  const { projectId } = useProjectParams();
  const { range } = useProjectFilters();
  const healthWindow = useHealthWindow();
  const query: ErrorTimeseriesQuery = {
    ...healthWindow,
    bucket: bucketFor(range.durationMs),
    provider,
  };
  return useQuery({
    queryKey: queryKeys.project(projectId).providerErrors(query),
    queryFn: () => providersApi.errorTimeseries(projectId, query),
    placeholderData: keepPreviousData,
  });
}

export interface SampleFilters {
  errorClass: ErrorClass | undefined;
  kind: RateLimitKind | undefined;
}

/** The 20 newest failed calls matching the filters (cut to the window's last 24 hours). */
export function useErrorSamples(filters: SampleFilters) {
  const { projectId } = useProjectParams();
  const healthWindow = useHealthWindow();
  const query: Omit<ErrorSamplesQuery, "cursor"> = {
    ...healthWindow,
    error_class: filters.errorClass,
    rate_limit_kind: filters.kind,
    limit: SAMPLE_LIMIT,
  };
  return useQuery({
    queryKey: queryKeys.project(projectId).providerSamples(query),
    queryFn: () => providersApi.samples(projectId, query),
    placeholderData: keepPreviousData,
  });
}

/**
 * Status page incidents overlapping the window, the ones that did not touch the API included (the
 * list greys them), and every feed's poll state.
 */
export function useProviderIncidents() {
  const { projectId } = useProjectParams();
  const { range } = useProjectFilters();
  const query: Omit<IncidentListQuery, "cursor"> = {
    from: range.from,
    to: range.to,
    api_only: false,
    limit: INCIDENT_LIMIT,
  };
  return useQuery({
    queryKey: queryKeys.project(projectId).providerIncidents(query),
    queryFn: () => providersApi.incidents(projectId, query),
    placeholderData: keepPreviousData,
  });
}
