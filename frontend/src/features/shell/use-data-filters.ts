import { useLocation } from "@tanstack/react-router";

import {
  DEFAULT_TIME_RANGE_OPTIONS,
  GATEWAY_TIME_RANGE_OPTIONS,
  type TimeRangeOptions,
} from "./time-range-options";

function disabled(range: string, environment?: string): TimeRangeOptions {
  return {
    ...DEFAULT_TIME_RANGE_OPTIONS,
    rangeDisabled: range,
    ...(environment === undefined ? {} : { environmentDisabled: environment }),
  };
}

/** The Evaluate and Prompts pages, by their first path segment, as their reasons name them. */
const LIBRARY_PAGES: Record<string, string> = {
  queues: "Review queues",
  annotate: "Review queues",
  datasets: "Datasets",
  experiments: "Experiments",
  evaluators: "Evaluators",
  prompts: "Prompts",
  playground: "Playground runs",
};

const SETTINGS_ENVIRONMENT = "Settings aren't filtered by environment.";

/**
 * The header contract (spec §5): which of the range and environment controls the page at
 * `pathname` (`/{org}/{project}/...`) honours, and why it doesn't honour the others.
 */
export function dataFiltersFor(pathname: string): TimeRangeOptions {
  const [page = "", sub, detail] = pathname.split("/").filter(Boolean).slice(2);
  const library = LIBRARY_PAGES[page];
  if (library !== undefined) {
    return disabled(
      `${library} aren't filtered by time.`,
      `${library} aren't filtered by environment.`,
    );
  }
  switch (page) {
    case "gateway":
      return sub === undefined
        ? GATEWAY_TIME_RANGE_OPTIONS
        : disabled(
            "Gateway settings aren't filtered by time.",
            "Gateway settings aren't filtered by environment.",
          );
    case "traces":
      return sub === undefined
        ? DEFAULT_TIME_RANGE_OPTIONS
        : disabled("A trace is shown whole. The range filters lists.", "A trace is shown whole.");
    case "sessions":
      return sub === undefined
        ? DEFAULT_TIME_RANGE_OPTIONS
        : disabled("A session shows every turn, whatever the range.");
    case "doctor":
      return disabled(
        "Findings aren't filtered by time; each one has its own window.",
        "Findings cover every environment.",
      );
    case "alerts":
      return disabled(
        "Each rule watches its own window.",
        "Rules set their own environment filter.",
      );
    case "budgets":
      return disabled("Budgets track their own period.", "Budgets set their own scope.");
    case "settings":
      return disabled(
        sub === "billing-sources" && detail !== undefined
          ? "Reconciliation picks its own days."
          : "Settings aren't filtered by time.",
        SETTINGS_ENVIRONMENT,
      );
    default:
      // Overview, Users, Provider health, Releases and Spend read both.
      return DEFAULT_TIME_RANGE_OPTIONS;
  }
}

/** Whether at least one of the two controls is on; the phone filter row shows only then. */
export function hasEnabledFilter(options: TimeRangeOptions): boolean {
  return options.rangeDisabled === undefined || options.environmentDisabled === undefined;
}

/** The header controls for the current page (the shell nulls them while the org is 2FA-locked). */
export function useDataFilters(): TimeRangeOptions {
  return dataFiltersFor(useLocation({ select: (location) => location.pathname }));
}
