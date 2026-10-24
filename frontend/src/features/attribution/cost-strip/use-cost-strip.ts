import { useQueries, useQuery } from "@tanstack/react-query";

import { todayUtc, windowFromDays } from "@/features/billing-sources/window-picker";
import { useProjectFilters, useProjectParams } from "@/features/shell/project-context";
import {
  billingSourcesApi,
  isApiError,
  projectsApi,
  queryKeys,
  type AttributionQuery,
} from "@/lib/api";
import type { ResolvedRange } from "@/lib/time-range";

import { useAttributionQuery } from "../attribution-queries";
import { aggregateSources, costStripCards, reportWindow, type Billing } from "./cost-strip-data";

/** The sources list and their reports change a few times a day; the report is costly to build. */
const STALE_MS = 5 * 60_000;

/**
 * The org's billing sources that map this project, each with its reconciliation report over the
 * global range snapped to whole UTC days (≤ 31, ending today). `window` is null when the range
 * starts after today; the reports then stay unfetched.
 */
export function useMappedSourceReports(range: ResolvedRange) {
  const { orgId, projectId } = useProjectParams();
  const sources = useQuery({
    queryKey: queryKeys.org(orgId).billingSources,
    queryFn: () => billingSourcesApi.list(orgId),
    staleTime: STALE_MS,
  });
  const window = reportWindow(range, todayUtc());
  const span = window ? windowFromDays(window.first, window.last) : null;
  const mapped = (sources.data ?? []).filter((source) =>
    source.projects.some((project) => project.id === projectId),
  );
  const reports = useQueries({
    queries: mapped.map((source) => ({
      // The report page's own key (`useReconciliationQuery`), so both share one cache entry.
      queryKey: [
        ...queryKeys.org(orgId).billingSource(source.id),
        "reconciliation",
        span?.from,
        span?.to,
      ],
      queryFn: () =>
        billingSourcesApi.reconciliation(orgId, source.id, { from: span?.from, to: span?.to }),
      enabled: span !== null,
      staleTime: STALE_MS,
    })),
  });
  const items = mapped.flatMap((source, index) => {
    const report = reports[index];
    return report ? [{ source, report }] : [];
  });
  return { sources, window, items };
}

type MappedReports = ReturnType<typeof useMappedSourceReports>;

function billingState({ sources, window, items }: MappedReports): Billing {
  if (sources.isError) {
    const hidden = isApiError(sources.error) && [403, 404].includes(sources.error.status);
    return { state: hidden ? "forbidden" : "failed" };
  }
  if (sources.isPending) {
    return { state: "pending" };
  }
  if (items.length === 0) {
    return { state: "not_connected" };
  }
  if (window === null) {
    return { state: "no_window" };
  }
  if (items.some(({ report }) => report.isError)) {
    return { state: "failed" };
  }
  const loaded = items.flatMap(({ source, report }) =>
    report.data ? [{ name: source.name, report: report.data }] : [],
  );
  if (loaded.length < items.length) {
    return { state: "pending" };
  }
  return { state: "ready", aggregate: aggregateSources(loaded), capped: window.capped };
}

/**
 * The strip's reads: the attribution totals (the page's own query when given, so Spend makes no
 * extra request; else one row by trace name), the token mix and the mapped sources' reports.
 */
export function useCostStrip(attributionQuery?: Omit<AttributionQuery, "cursor">) {
  const { projectId } = useProjectParams();
  const { range, environment } = useProjectFilters();
  const attribution = useAttributionQuery(
    attributionQuery ?? {
      group_by: "trace_name",
      from: range.from,
      to: range.to,
      environment,
      limit: 1,
    },
  );
  const span = { from: range.from, to: range.to };
  const tokenMix = useQuery({
    queryKey: queryKeys.project(projectId).tokenMix(span),
    queryFn: () => projectsApi.tokenMix(projectId, span),
  });
  const mapped = useMappedSourceReports(range);
  const page = attribution.data?.pages[0];
  const cards = costStripCards({
    attribution: page,
    attributionFailed: attribution.isError && page === undefined,
    billing: billingState(mapped),
  });

  function retryBilling() {
    void mapped.sources.refetch();
    for (const { report } of mapped.items) {
      void report.refetch();
    }
  }
  return {
    cards,
    tokenMix,
    retry: { spend: () => void attribution.refetch(), billing: retryBilling },
  };
}
