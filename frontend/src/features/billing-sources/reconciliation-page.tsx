import { getRouteApi } from "@tanstack/react-router";
import { Receipt } from "lucide-react";
import { useMemo } from "react";

import { BackLink } from "@/components/back-link";
import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { Skeleton } from "@/components/ui/skeleton";
import { usePermission, useProjectParams } from "@/features/shell";
import { isApiError, type BillingSource } from "@/lib/api";

import { SourceCallout } from "./reconciliation-callouts";
import { tableFootnote } from "./reconciliation-copy";
import { DailyCard, DayTable, DayTableSkeleton } from "./reconciliation-day-table";
import { ReconciliationHeader } from "./reconciliation-header";
import { useBillingSourceQuery, useReconciliationQuery } from "./reconciliation-queries";
import { flattenDays, summaryTiles } from "./reconciliation-table";
import { ReconciliationTiles, ReconciliationTilesSkeleton } from "./reconciliation-tiles";
import { windowFromSearch, windowLabel, type ReportWindow } from "./window-picker";

const route = getRouteApi("/_authed/$orgId/$projectId/settings/billing-sources/$sourceId");

const REPORT_ERROR =
  "The request failed. Check your connection and try again. Provider data already fetched is kept.";

/**
 * Figma "Reconciliation / *" (spec §11.3): one billing source's bill against Spanlight's own
 * figures over a window of whole UTC days. The page composes; the parts render and the queries
 * fetch. The source loads first (header, callouts); the report follows (tiles, daily table).
 */
export function ReconciliationPage() {
  const { sourceId } = route.useParams();
  const search = route.useSearch();
  const navigate = route.useNavigate();
  const reportWindow = useMemo(() => windowFromSearch(search), [search]);
  const sourceQuery = useBillingSourceQuery(sourceId);

  if (sourceQuery.isPending) {
    return <PageSkeleton />;
  }
  if (sourceQuery.isError) {
    return (
      <SourceError
        error={sourceQuery.error}
        retrying={sourceQuery.isFetching}
        onRetry={() => void sourceQuery.refetch()}
      />
    );
  }
  return (
    <ReconciliationContent
      source={sourceQuery.data}
      reportWindow={reportWindow}
      onWindowChange={(days) => {
        void navigate({ search: (previous) => ({ ...previous, ...days }) });
      }}
    />
  );
}

interface ContentProps {
  source: BillingSource;
  reportWindow: ReportWindow;
  onWindowChange: (days: { first: string; last: string }) => void;
}

function ReconciliationContent({ source, reportWindow, onWindowChange }: ContentProps) {
  const canManage = usePermission("billing_sources:manage");
  const report = useReconciliationQuery(source.id, reportWindow);
  const rows = useMemo(() => (report.data ? flattenDays(report.data) : []), [report.data]);
  const tiles = useMemo(() => (report.data ? summaryTiles(report.data) : []), [report.data]);

  return (
    <div className="flex flex-col gap-6">
      <ReconciliationHeader
        source={source}
        reportWindow={reportWindow}
        onWindowChange={onWindowChange}
        canManage={canManage}
      />
      <SourceCallout source={source} report={report.data} canManage={canManage} />
      {report.isPending ? (
        <>
          <ReconciliationTilesSkeleton />
          <DailyCard>
            <DayTableSkeleton label={`Loading the report for ${windowLabel(reportWindow)}…`} />
          </DailyCard>
        </>
      ) : report.isError ? (
        <DailyCard legend={false}>
          <ErrorState
            compact
            error={report.error}
            title="Couldn’t load the reconciliation report"
            description={
              isApiError(report.error) && report.error.status === 422 ? undefined : REPORT_ERROR
            }
            retrying={report.isFetching}
            onRetry={() => void report.refetch()}
            className="rounded-tile bg-surface-muted py-12"
          />
        </DailyCard>
      ) : rows.length === 0 ? (
        <DailyCard legend={false}>
          <EmptyState
            icon={Receipt}
            title="Nothing to compare in this window"
            description={`Neither ${source.name} nor Spanlight has any cost for its projects between ${windowLabel(reportWindow).replace(/ · .*$/, "")}. Pick another window, or check which projects the source compares.`}
            className="rounded-tile bg-surface-muted"
          />
        </DailyCard>
      ) : (
        <>
          <ReconciliationTiles tiles={tiles} />
          <DailyCard footnote={tableFootnote(source)}>
            <DayTable rows={rows} />
          </DailyCard>
        </>
      )}
    </div>
  );
}

interface SourceErrorProps {
  error: unknown;
  retrying: boolean;
  onRetry: () => void;
}

/** The source itself failed to load: not found (no retry), or any other failure with a retry. */
function SourceError({ error, retrying, onRetry }: SourceErrorProps) {
  const { orgId, projectId } = useProjectParams();
  const missing = isApiError(error) && error.status === 404;
  return (
    <div className="flex flex-col gap-6">
      <BackLink to="/$orgId/$projectId/settings/billing-sources" params={{ orgId, projectId }}>
        Billing sources
      </BackLink>
      <ErrorState
        error={error}
        title={missing ? "Billing source not found" : "Couldn't load this billing source"}
        description={
          missing ? "It may have been deleted, or it belongs to another organization." : undefined
        }
        retrying={retrying}
        onRetry={missing ? undefined : onRetry}
      />
    </div>
  );
}

/** While the source loads: the header's shape, then the report's loading state. */
function PageSkeleton() {
  return (
    <div role="status" aria-label="Loading the billing source" className="flex flex-col gap-6">
      <Skeleton className="h-4 w-28" />
      <div className="flex flex-col gap-2">
        <Skeleton className="h-9 w-64" />
        <Skeleton className="h-4 w-96 max-w-full" />
      </div>
      <ReconciliationTilesSkeleton />
    </div>
  );
}
