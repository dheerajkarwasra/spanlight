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
  if (overview.data.current.traces > 0) {
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
  switch (state) {
    case "loading":
    case "error":
      return null;
    case "first-run":
      return heroStatus({ traces: 0, errors: 0, hasEverReceivedTraces: false });
    case "empty-window":
      return heroStatus({ traces: 0, errors: 0, hasEverReceivedTraces: true });
    case "ready":
      // Unknown findings must not read as "behaving"; a failed health check counts none.
      if (queries.health.isPending) {
        return null;
      }
      return heroStatus({
        traces: current?.traces ?? 0,
        errors: current ? (errorCountFrom(current.error_rate, current.llm_calls) ?? 0) : 0,
        findings:
          (queries.health.data?.open_critical ?? 0) + (queries.health.data?.open_warning ?? 0),
        hasEverReceivedTraces: true,
      });
  }
}
