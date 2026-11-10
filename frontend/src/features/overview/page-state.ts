import { sourceHistoryLabel } from "@/lib/freshness";

import { errorCountFrom, heroStatus, type HeroStatus } from "./hero";
import type { OverviewQueries } from "./use-overview-queries";

/** What the overview shows, from the queries: placeholders, an error, a setup screen or data. */
export type PageState = "loading" | "error" | "first-run" | "empty-window" | "ready";

export function pageStateOf({ overview, onboarding }: OverviewQueries): PageState {
  if (overview.isPending) {
    return "loading";
  }
  if (overview.isError) {
    return "error";
  }
  if (overview.data.current.traces !== 0) {
    return "ready";
  }
  if (onboarding.isPending) {
    return "loading";
  }
  // If the onboarding check fails, assume the project has data elsewhere: suggesting a wider
  // range is harmless, a false "waiting for your first trace" is not.
  return onboarding.data?.has_traces === false ? "first-run" : "empty-window";
}

export function statusFor(state: PageState, queries: OverviewQueries): HeroStatus | null {
  const current = queries.overview.data?.current;
  if (
    current?.traces === null ||
    current?.llm_calls === null ||
    current?.aggregate_status === "unavailable"
  )
    return null;
  switch (state) {
    case "loading":
    case "error":
      return null;
    case "first-run":
      return heroStatus({ traces: 0, errors: 0, hasEverReceivedTraces: false });
    case "empty-window":
      return heroStatus({ traces: 0, errors: 0, hasEverReceivedTraces: true });
    case "ready": {
      // Unknown findings must not read as "behaving"; a failed health check, even with a cached
      // response, counts none and says the Doctor's health is unavailable.
      if (queries.health.isPending) {
        return null;
      }
      const health = queries.health.isError ? undefined : queries.health.data;
      return heroStatus({
        traces: current?.traces ?? 0,
        errors: current ? errorCountFrom(current.error_rate, current.llm_calls) : 0,
        findings: (health?.open_critical ?? 0) + (health?.open_warning ?? 0),
        healthUnavailable: health === undefined,
        hasEverReceivedTraces: true,
      });
    }
  }
}

/**
 * "Retained snapshot" or "Source history unavailable" for the hero's meta line when any of the
 * page's aggregate reads lost its source history; null otherwise (complete, pending, legacy).
 */
export function historyLabelFor({ overview, timeseries, models }: OverviewQueries): string | null {
  const current = overview.data?.current;
  const unavailable = [current, timeseries.data, models.data].some(
    (read) => read?.aggregate_status === "unavailable",
  );
  const hasFigures =
    current?.traces != null ||
    current?.llm_calls != null ||
    // An hourly bucket whose values are all null holds no stored figure.
    (timeseries.data?.items ?? []).some(
      (point) =>
        point.llm_calls != null ||
        point.errors != null ||
        point.p95_ms != null ||
        point.cost_usd != null ||
        point.tokens != null,
    ) ||
    (models.data?.items.length ?? 0) > 0;
  return sourceHistoryLabel(unavailable ? "unavailable" : undefined, hasFigures);
}
