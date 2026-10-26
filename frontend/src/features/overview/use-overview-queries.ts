import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";

import { useAlertRulesQuery } from "@/features/alerts";
import { useBudgetsQuery } from "@/features/budgets";
import { useProjectFilters, useProjectParams } from "@/features/shell/project-context";
import {
  completenessApi,
  insightsApi,
  projectsApi,
  providersApi,
  queryKeys,
  releasesApi,
  type CompletenessQuery,
  type IncidentListQuery,
  type MetricsQuery,
  type ReleaseListQuery,
  type TraceListQuery,
} from "@/lib/api";
import { bucketFor, resolveRange } from "@/lib/time-range";

import { RELEASE_WINDOW_MAX_MS } from "./release-markers";
import { PROJECTION_MAX_WINDOW_MS, utcMonthStart } from "./spend";

export const RECENT_ERRORS_LIMIT = 3;
export const TAB_TRACES_LIMIT = 5;

/**
 * The open findings Needs attention picks its top 3 from by severity: enough that an older
 * critical finding is not hidden behind newer warnings.
 */
export const OPEN_INSIGHTS_LIMIT = 20;

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

  // The UTC month's spend so far, for the Spend card's projection. Preset windows end now;
  // longer or custom windows show no projection, so they skip it.
  const monthToDateWindow: MetricsQuery = {
    from: new Date(utcMonthStart(new Date(Date.parse(range.to) - 1))).toISOString(),
    to: range.to,
    environment,
  };
  const monthToDate = useQuery({
    queryKey: keys.overview(monthToDateWindow),
    queryFn: () => projectsApi.overview(projectId, monthToDateWindow),
    enabled: range.value !== "custom" && range.durationMs <= PROJECTION_MAX_WINDOW_MS,
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

  // Open and acknowledged findings for Needs attention, most recently seen first.
  const openInsightsQuery = {
    status: ["open", "acknowledged"],
    limit: OPEN_INSIGHTS_LIMIT,
  } as const;
  const openInsights = useQuery({
    queryKey: keys.insights(openInsightsQuery),
    queryFn: () => insightsApi.list(projectId, openInsightsQuery),
  });

  // Release markers: the releases API answers 422 above 30 days, so longer windows skip it (and
  // `windowMarkers` ignores the placeholder a disabled query still holds).
  const releasesQuery: ReleaseListQuery = { from: range.from, to: range.to, environment };
  const releases = useQuery({
    queryKey: keys.releases(releasesQuery),
    queryFn: () => releasesApi.list(projectId, releasesQuery),
    enabled: range.durationMs <= RELEASE_WINDOW_MAX_MS,
    placeholderData: keepPreviousData,
  });

  const alertRules = useAlertRulesQuery();
  const budgets = useBudgetsQuery();

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
    overview,
    monthToDate,
    timeseries,
    models,
    health,
    detectorRuns,
    openInsights,
    completeness,
    incidents,
    releases,
    alertRules,
    budgets,
    onboarding,
  };
}

export type OverviewQueries = ReturnType<typeof useOverviewQueries>;

export type TracesTab = "errors" | "slowest" | "latest";

/** The slowest traces first: the Slowest tab's list and the p95 card's link (desc is the default order). */
export const SLOWEST_SORT = { sort: "duration" } as const;

/**
 * One tab of the Traces card. Each tab's panel calls this, and Radix unmounts the panels that are
 * not shown, so a tab fetches when opened and Latest polls only while its tab is open.
 */
export function useTracesTabQuery(tab: TracesTab) {
  const { projectId } = useProjectParams();
  const { range, environment } = useProjectFilters();
  const keys = queryKeys.project(projectId);
  // Preset windows end "now", so the latest traces poll and re-resolve the window on every
  // fetch: a trace sent a minute after the page loaded still shows up. Custom windows are
  // fixed in the past and fetched once.
  const isLive = tab === "latest" && range.value !== "custom";
  const query: Omit<TraceListQuery, "cursor"> = {
    from: range.from,
    to: range.to,
    environment,
    limit: tab === "errors" ? RECENT_ERRORS_LIMIT : TAB_TRACES_LIMIT,
    status: tab === "errors" ? "error" : undefined,
    ...(tab === "slowest" ? SLOWEST_SORT : {}),
  };
  const result = useQuery({
    queryKey: isLive
      ? [...keys.all, "latest-traces", { range: range.value, environment, limit: TAB_TRACES_LIMIT }]
      : keys.traces(query),
    queryFn: () => {
      const window = isLive ? resolveRange({ range: range.value }) : range;
      return projectsApi.traces(projectId, { ...query, from: window.from, to: window.to });
    },
    refetchInterval: isLive ? LIVE_REFRESH_MS : false,
    placeholderData: keepPreviousData,
  });
  return { ...result, isLive };
}
