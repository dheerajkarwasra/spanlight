import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";

import { useProjectFilters, useProjectParams } from "@/features/shell/project-context";
import {
  completenessApi,
  insightsApi,
  projectsApi,
  providersApi,
  queryKeys,
  type CompletenessQuery,
  type IncidentListQuery,
  type MetricsQuery,
  type TraceListQuery,
} from "@/lib/api";
import { bucketFor, resolveRange } from "@/lib/time-range";

export const RECENT_ERRORS_LIMIT = 3;
export const LATEST_TRACES_LIMIT = 5;

/** The most incidents the calls/errors overlay asks for (the API's maximum). */
export const INCIDENT_OVERLAY_LIMIT = 100;

/** How often the "Live" latest-traces list refreshes. */
export const LIVE_REFRESH_MS = 15_000;

/** The newest detector runs the Doctor card reads its last pass from (one pass is ~15 runs). */
export const DETECTOR_RUNS_LIMIT = 20;

/** How often the first-run screen checks whether the first trace has arrived. */
export const FIRST_TRACE_POLL_MS = 3_000;

/**
 * Every query the overview needs, keyed by the URL's time window and environment.
 * `keepPreviousData` holds the last render while a new range loads, so switching ranges dims
 * the page instead of flashing skeletons.
 */
export function useOverviewQueries() {
  const queryClient = useQueryClient();
  const { projectId } = useProjectParams();
  const { range, environment } = useProjectFilters();
  const keys = queryKeys.project(projectId);
  const metricsWindow: MetricsQuery = { from: range.from, to: range.to, environment };
  const bucket = bucketFor(range.durationMs);

  const overview = useQuery({
    queryKey: keys.overview(metricsWindow),
    queryFn: () => projectsApi.overview(projectId, metricsWindow),
    placeholderData: keepPreviousData,
  });

  const timeseries = useQuery({
    queryKey: keys.timeseries({ ...metricsWindow, bucket }),
    queryFn: () => projectsApi.timeseries(projectId, { ...metricsWindow, bucket }),
    placeholderData: keepPreviousData,
  });

  const models = useQuery({
    queryKey: keys.models(metricsWindow),
    queryFn: () => projectsApi.models(projectId, metricsWindow),
    placeholderData: keepPreviousData,
  });

  // The Doctor's score for the same window and environment as the rest of the page.
  const health = useQuery({
    queryKey: keys.health(metricsWindow),
    queryFn: () => insightsApi.health(projectId, metricsWindow),
    placeholderData: keepPreviousData,
  });

  // The last detector pass. Its own key under the Doctor prefix: the Doctor page caches one run.
  const detectorRuns = useQuery({
    queryKey: [...keys.detectorRuns, DETECTOR_RUNS_LIMIT],
    queryFn: () => insightsApi.detectorRuns(projectId, DETECTOR_RUNS_LIMIT),
    placeholderData: keepPreviousData,
  });

  // The most recently seen open finding, asked for only while there are open findings.
  const topInsightQuery = { status: ["open", "acknowledged"], limit: 1 } as const;
  const openFindings = (health.data?.open_critical ?? 0) + (health.data?.open_warning ?? 0);
  const topInsight = useQuery({
    queryKey: keys.insights(topInsightQuery),
    queryFn: () => insightsApi.list(projectId, topInsightQuery),
    select: (page) => page.items[0],
    enabled: openFindings > 0,
  });

  // Losses are counted per project, not per environment, so the window alone scopes them.
  const completenessQuery: CompletenessQuery = { from: range.from, to: range.to };
  const completeness = useQuery({
    queryKey: keys.completeness(completenessQuery),
    queryFn: () => completenessApi.get(projectId, completenessQuery),
    placeholderData: keepPreviousData,
  });

  // Status page incidents are global; the API's default keeps the API-affected ones, and the
  // overlay keeps those of the providers the models list shows.
  const incidentsQuery: Omit<IncidentListQuery, "cursor"> = {
    from: range.from,
    to: range.to,
    limit: INCIDENT_OVERLAY_LIMIT,
  };
  const incidents = useQuery({
    queryKey: keys.providerIncidents(incidentsQuery),
    queryFn: () => providersApi.incidents(projectId, incidentsQuery),
    placeholderData: keepPreviousData,
  });

  const errorsQuery: Omit<TraceListQuery, "cursor"> = {
    ...metricsWindow,
    status: "error",
    limit: RECENT_ERRORS_LIMIT,
  };
  const recentErrors = useQuery({
    queryKey: keys.traces(errorsQuery),
    queryFn: () => projectsApi.traces(projectId, errorsQuery),
    placeholderData: keepPreviousData,
  });

  // Preset windows end "now", so the latest traces poll and re-resolve the window on every
  // fetch: a trace sent a minute after the page loaded still shows up. Custom windows are
  // fixed in the past and fetched once.
  const isLive = range.value !== "custom";
  const latestTraces = useQuery({
    queryKey: [
      ...keys.all,
      "latest-traces",
      isLive
        ? { range: range.value, environment, limit: LATEST_TRACES_LIMIT }
        : { ...metricsWindow, limit: LATEST_TRACES_LIMIT },
    ],
    queryFn: () => {
      const window = isLive ? resolveRange({ range: range.value }) : range;
      return projectsApi.traces(projectId, {
        from: window.from,
        to: window.to,
        environment,
        limit: LATEST_TRACES_LIMIT,
      });
    },
    refetchInterval: isLive ? LIVE_REFRESH_MS : false,
    placeholderData: keepPreviousData,
  });

  // Only needed to tell "never sent a trace" apart from "nothing in this range". While the
  // project has no traces at all, keep checking so the first one replaces the setup screen.
  const rangeIsEmpty = overview.data?.current.traces === 0;
  const onboarding = useQuery({
    queryKey: keys.onboarding,
    queryFn: () => projectsApi.onboarding(projectId),
    enabled: rangeIsEmpty,
    refetchInterval: (query) =>
      query.state.data?.has_traces === false ? FIRST_TRACE_POLL_MS : false,
  });

  // The first trace just arrived: everything on the page is stale.
  const hasTraces = onboarding.data?.has_traces;
  const previousHasTraces = useRef(hasTraces);
  useEffect(() => {
    if (previousHasTraces.current === false && hasTraces === true) {
      void queryClient.invalidateQueries({ queryKey: queryKeys.project(projectId).all });
    }
    previousHasTraces.current = hasTraces;
  }, [hasTraces, projectId, queryClient]);

  return {
    range,
    environment,
    bucket,
    isLive,
    overview,
    timeseries,
    models,
    health,
    detectorRuns,
    topInsight,
    completeness,
    incidents,
    recentErrors,
    latestTraces,
    onboarding,
  };
}

export type OverviewQueries = ReturnType<typeof useOverviewQueries>;
