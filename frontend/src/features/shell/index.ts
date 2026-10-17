/**
 * What other features may use from `features/shell`: the signed-in frame's hooks (the current
 * project, organization, role and filters), the time-range options and the account frame. Import from this file, not from the
 * modules behind it.
 */
export { AccountShell } from "./account-shell";
export {
  useCurrentOrg,
  useCurrentRole,
  useFilterOptionsQuery,
  useOrgProjectsQuery,
  usePermission,
  useProjectFilters,
  useProjectParams,
  useProjectQuery,
} from "./project-context";
export {
  DEFAULT_TIME_RANGE_OPTIONS,
  GATEWAY_TIME_RANGE_OPTIONS,
  type TimeRangeOptions,
} from "./time-range-options";
export { useSignOut } from "./use-sign-out";
