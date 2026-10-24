import { Link } from "@tanstack/react-router";

import { Breadcrumbs, Crumb } from "@/components/breadcrumbs";
import { Button } from "@/components/ui/button";
import { useProjectParams } from "@/features/shell";
import type { BillingSource } from "@/lib/api";

import { csvDownload } from "./download-csv";
import { RefreshButton } from "./refresh-button";
import { sourceSummary } from "./reconciliation-copy";
import { SourceStatus } from "./source-status";
import { UploadCsvDialog } from "./upload-csv-dialog";
import { WindowPicker } from "./window-picker-popover";
import type { ReportWindow } from "./window-picker";

interface ReconciliationHeaderProps {
  source: BillingSource;
  reportWindow: ReportWindow;
  onWindowChange: (days: { first: string; last: string }) => void;
  /** Admins and owners refresh or upload; every member reads and downloads. */
  canManage: boolean;
}

/** Refresh for an API source, Upload CSV for a CSV source; nothing for a member. */
function FetchAction({ source }: { source: BillingSource }) {
  if (source.kind !== "csv_upload") {
    return <RefreshButton source={source} />;
  }
  return (
    <UploadCsvDialog
      source={source}
      trigger={
        <Button variant="secondary" className="@max-[40rem]:h-12">
          Upload CSV
        </Button>
      }
    />
  );
}

/**
 * Figma "Header bar" (717:1236): breadcrumbs back to Cost › Reconciliation, the source's name and
 * status, what it compares since when, then the window picker, Refresh (or Upload CSV), Download
 * CSV and Manage source (Settings › Billing sources). On a phone the picker takes a row and the
 * buttons share the next rows (Figma "mobile 375").
 */
export function ReconciliationHeader({
  source,
  reportWindow,
  onWindowChange,
  canManage,
}: ReconciliationHeaderProps) {
  const { orgId, projectId } = useProjectParams();
  const download = csvDownload(orgId, source.id, reportWindow);

  return (
    <div className="@container flex flex-col gap-6">
      <Breadcrumbs current={source.name}>
        <Crumb to="/$orgId/$projectId/reconciliation" params={{ orgId, projectId }}>
          Reconciliation
        </Crumb>
      </Breadcrumbs>
      <div className="flex flex-col gap-4 @[52rem]:flex-row @[52rem]:items-start @[52rem]:gap-6">
        <div className="flex min-w-0 flex-1 flex-col gap-2">
          <div className="flex flex-col items-start gap-2 sm:flex-row sm:items-center sm:gap-3">
            <h1 className="text-h1 [overflow-wrap:anywhere] text-foreground">{source.name}</h1>
            <SourceStatus source={source} />
          </div>
          <p className="text-sm text-muted-foreground">{sourceSummary(source)}</p>
        </div>
        <div className="flex flex-col gap-2 @[40rem]:flex-row @[40rem]:items-center">
          <WindowPicker value={reportWindow} onChange={onWindowChange} />
          <div className="grid grid-cols-2 gap-2 @[40rem]:flex [&_a]:w-full @[40rem]:[&_a]:w-auto [&_button]:w-full @[40rem]:[&_button]:w-auto">
            {canManage ? <FetchAction source={source} /> : null}
            <Button
              variant="secondary"
              asChild
              className={canManage ? "@max-[40rem]:h-12" : "col-span-2 @max-[40rem]:h-12"}
            >
              <a href={download.href} download>
                Download CSV<span className="sr-only"> {download.windowText}</span>
              </a>
            </Button>
            <Button variant="secondary" asChild className="col-span-2 @max-[40rem]:h-12">
              <Link to="/$orgId/$projectId/settings/billing-sources" params={{ orgId, projectId }}>
                Manage source
              </Link>
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
