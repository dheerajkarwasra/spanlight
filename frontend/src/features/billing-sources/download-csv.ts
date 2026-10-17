import { billingSourcesApi } from "@/lib/api";

import { windowLabel, type ReportWindow } from "./window-picker";

/**
 * "Download CSV" is a plain link to the CSV route, not a fetch: the browser saves the file under
 * the name the server gives it (`Content-Disposition`), the session cookie authenticates the GET,
 * and nothing of the bill stays in memory or in a blob URL.
 */
export function csvDownload(
  orgId: string,
  sourceId: string,
  reportWindow: ReportWindow,
): { href: string; windowText: string } {
  return {
    href: billingSourcesApi.reconciliationCsvUrl(orgId, sourceId, {
      from: reportWindow.from,
      to: reportWindow.to,
    }),
    windowText: `for ${windowLabel(reportWindow)}`,
  };
}
