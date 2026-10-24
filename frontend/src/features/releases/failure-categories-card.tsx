import { Link } from "@tanstack/react-router";
import { ClipboardCheck, Info, ListChecks } from "lucide-react";
import { useId } from "react";

import { ChartDataTableView } from "@/components/chart-data-table";
import { ChartViewToggle } from "@/components/chart-view-toggle";
import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { Button } from "@/components/ui/button";
import { Card, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { FailureCategoriesDialog } from "@/features/scores";
import { usePermission, useProjectParams } from "@/features/shell";
import type { FailureCategoryRates } from "@/lib/api";
import { useChartView, type ChartView } from "@/lib/use-stored-choice";
import { cn } from "@/lib/utils";

import { FailureCategoriesChart } from "./failure-categories-chart";
import { FailureCategoriesTable } from "./failure-categories-table";
import {
  CHART_RELEASE_LIMIT,
  NONE_CATEGORY,
  NO_RELEASE_LABEL,
  categoriesOf,
  chartSeries,
  dataTable,
  emptyState,
  fewItems,
} from "./failure-categories";
import { useFailureCategoriesQuery, useHasFailureCategories } from "./releases-queries";

/** Per viewer, per browser: the card opens the way it was left. */
const VIEW_KEY = "spanlight-release-failure-categories-view";

/**
 * Figma "Failure categories card": the share of reviewed traces per failure category and release,
 * with Wilson 95% intervals, in the page's window and environment. A "Show table" view carries
 * the exact numbers and is the card's form at 375 px.
 */
export function FailureCategoriesCard() {
  const titleId = useId();
  const [view, setView] = useChartView(VIEW_KEY);
  const configured = useHasFailureCategories();
  const query = useFailureCategoriesQuery(configured === true);
  const state = configured === false ? "no_categories" : emptyState(query.data, query.error);
  const hasRates = query.data !== undefined && state === null;

  return (
    <Card role="region" aria-labelledby={titleId} className="flex flex-col gap-4 p-5 sm:p-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start">
        <div className="flex min-w-0 flex-1 flex-col gap-1">
          <CardTitle id={titleId}>Failure categories</CardTitle>
          <p className="text-xs font-medium text-muted-foreground">
            Share of reviewed traces per category and release · 95% intervals
          </p>
        </div>
        {hasRates ? (
          <div className="shrink-0 max-sm:hidden">
            <ChartViewToggle view={view} onChange={setView} />
          </div>
        ) : null}
      </div>
      <div className={cn("transition-opacity", query.isPlaceholderData && "opacity-60")}>
        {state === "no_categories" ? (
          <NoCategories />
        ) : query.isPending ? (
          <Skeleton
            role="status"
            aria-label="Loading failure categories"
            className="h-[300px] rounded-tile"
          />
        ) : query.data === undefined ? (
          <ErrorState
            compact
            error={query.error}
            title="Couldn't load failure categories"
            onRetry={() => void query.refetch()}
          />
        ) : state === "no_reviewed" ? (
          <NoReviewed />
        ) : (
          <RatesBody data={query.data} view={view} />
        )}
      </div>
    </Card>
  );
}

function RatesBody({ data, view }: { data: FailureCategoryRates; view: ChartView }) {
  const table = dataTable(data);
  const reviewed = data.releases.filter((release) => release.reviewed > 0);
  const charted = reviewed
    .slice(0, CHART_RELEASE_LIMIT)
    .map((release) => release.release ?? NO_RELEASE_LABEL);
  const categories = categoriesOf(data).filter((name) => name !== NONE_CATEGORY);
  const few = reviewed.filter((release) => fewItems(release.reviewed));
  const summary = `Share of reviewed traces per failure category for ${charted.join(", ")}, with 95% intervals. The table view lists every value.`;
  const tableView = <FailureCategoriesTable table={table} />;

  return (
    <div className="flex flex-col gap-4">
      {view === "table" ? (
        tableView
      ) : (
        <>
          <div className="max-sm:hidden">
            <FailureCategoriesChart
              releases={charted}
              categories={categories}
              points={chartSeries(data)}
              summary={summary}
            />
            <ChartDataTableView table={table} />
          </div>
          <div className="sm:hidden">{tableView}</div>
        </>
      )}
      {few.length > 0 ? (
        <p className="flex items-start gap-2 text-xs font-medium text-muted-foreground">
          <Info aria-hidden className="mt-px size-3.5 shrink-0" />
          {`Few items: under 10 reviewed traces in ${few
            .map(
              (release) =>
                `${release.release ?? NO_RELEASE_LABEL} (n = ${String(release.reviewed)})`,
            )
            .join(", ")}. Read those intervals as rough.`}
        </p>
      ) : null}
      <p className="text-xs font-medium text-subtle-foreground">
        {view === "chart" ? (
          <span className="max-sm:hidden">
            Bars leave out none (passed review); the table lists it.
            {reviewed.length > charted.length
              ? ` The chart shows the ${String(charted.length)} latest releases with reviewed traces; the table lists all ${String(reviewed.length)}.`
              : ""}{" "}
          </span>
        ) : null}
        Each value is count (rate, 95% interval) of the release&apos;s reviewed traces.
      </p>
    </div>
  );
}

function NoCategories() {
  const canWrite = usePermission("scores:write");
  return (
    <EmptyState
      icon={ListChecks}
      title="No failure categories yet"
      description={
        canWrite
          ? "Set up the categories reviewers label traces with; each one's rate per release shows here."
          : "A member sets up the categories reviewers label traces with; each one's rate per release shows here."
      }
      className="rounded-tile bg-surface-muted py-10"
      action={
        canWrite ? (
          <FailureCategoriesDialog
            trigger={<Button variant="primary">Set up failure categories</Button>}
          />
        ) : null
      }
    />
  );
}

function NoReviewed() {
  const { orgId, projectId } = useProjectParams();
  return (
    <EmptyState
      icon={ClipboardCheck}
      title="No reviewed traces in this window"
      description={
        <>
          Label <code className="font-mono text-xs">failure_category</code> on traces in a review
          queue, or widen the window.
        </>
      }
      className="rounded-tile bg-surface-muted py-10"
      action={
        <Button asChild variant="secondary">
          <Link to="/$orgId/$projectId/queues" params={{ orgId, projectId }}>
            Open Daily review
          </Link>
        </Button>
      }
    />
  );
}
