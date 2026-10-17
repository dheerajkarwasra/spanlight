import { useMatchRoute } from "@tanstack/react-router";

import {
  DEFAULT_TIME_RANGE_OPTIONS,
  GATEWAY_TIME_RANGE_OPTIONS,
  type TimeRangeOptions,
  USERS_TIME_RANGE_OPTIONS,
} from "./time-range-options";

/**
 * Pages that show time-windowed data get the range and environment controls. The gateway overview
 * (the exact `/gateway` route, not Keys, Routes, Credentials or Lab) offers only the windows its
 * endpoint accepts; the end-user pages get the range without the environment filter.
 */
export function useDataFilters(): TimeRangeOptions | null {
  const matchRoute = useMatchRoute();
  if (matchRoute({ to: "/$orgId/$projectId/gateway", fuzzy: false })) {
    return GATEWAY_TIME_RANGE_OPTIONS;
  }
  // Fuzzy, so the user detail page (`/users/$userId`) gets the same control.
  if (matchRoute({ to: "/$orgId/$projectId/users", fuzzy: true })) {
    return USERS_TIME_RANGE_OPTIONS;
  }
  const windowed =
    matchRoute({ to: "/$orgId/$projectId/overview" }) ||
    matchRoute({ to: "/$orgId/$projectId/traces" }) ||
    matchRoute({ to: "/$orgId/$projectId/sessions" }) ||
    matchRoute({ to: "/$orgId/$projectId/providers" }) ||
    matchRoute({ to: "/$orgId/$projectId/releases" });
  return windowed ? DEFAULT_TIME_RANGE_OPTIONS : null;
}
