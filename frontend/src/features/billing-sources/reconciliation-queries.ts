import { useQuery } from "@tanstack/react-query";

import { useProjectParams } from "@/features/shell";
import { billingSourcesApi, queryKeys } from "@/lib/api";

import type { ReportWindow } from "./window-picker";

/**
 * The reconciliation page's reads. Both sit under the org's billing-sources key, so a refresh,
 * an upload or an edit (which invalidate that key) reloads the source and its report.
 */

export function useBillingSourceQuery(sourceId: string) {
  const { orgId } = useProjectParams();
  return useQuery({
    queryKey: queryKeys.org(orgId).billingSource(sourceId),
    queryFn: () => billingSourcesApi.get(orgId, sourceId),
  });
}

export function useReconciliationQuery(sourceId: string, reportWindow: ReportWindow) {
  const { orgId } = useProjectParams();
  return useQuery({
    queryKey: [
      ...queryKeys.org(orgId).billingSource(sourceId),
      "reconciliation",
      reportWindow.from,
      reportWindow.to,
    ],
    queryFn: () =>
      billingSourcesApi.reconciliation(orgId, sourceId, {
        from: reportWindow.from,
        to: reportWindow.to,
      }),
  });
}
