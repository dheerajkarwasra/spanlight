import { Link } from "@tanstack/react-router";
import { Receipt } from "lucide-react";

import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { PageHeader } from "@/components/page-header";
import { SectionCard } from "@/components/section-card";
import { TileListSkeleton } from "@/components/tile-list";
import { Button } from "@/components/ui/button";
import { CostStrip, useMappedSourceReports } from "@/features/attribution/cost-strip";
import { useProjectFilters, useProjectParams } from "@/features/shell";
import { isApiError } from "@/lib/api";

import { SourceSummaryTable } from "./source-summary-table";

/**
 * Cost › Reconciliation (Figma 717:559, spec §3.5): the cost strip, then the billing sources
 * that map this project over the global range snapped to whole UTC days, each opening its
 * report. Reads the same queries as the strip, so the page makes no extra request.
 */
export function ReconciliationTabPage() {
  const { range } = useProjectFilters();
  const mapped = useMappedSourceReports(range);

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Reconciliation"
        description="What each provider billed against what Spanlight computed, per billing source. A source's figures cover every project it maps."
      />
      <CostStrip />
      <SectionCard title="Billing sources" className="gap-4">
        <SourcesBody mapped={mapped} />
      </SectionCard>
    </div>
  );
}

function SourcesBody({ mapped }: { mapped: ReturnType<typeof useMappedSourceReports> }) {
  const { sources, window, items } = mapped;
  const params = useProjectParams();

  if (sources.isPending) {
    return <TileListSkeleton label="Loading billing sources" rows={2} />;
  }
  // The list needs `org:read`: a 403/404 means this member can't see the sources, not a failure.
  const hidden =
    sources.isError && isApiError(sources.error) && [403, 404].includes(sources.error.status);
  if (sources.isError && !hidden) {
    return (
      <ErrorState
        compact
        error={sources.error}
        title="Couldn't load billing sources"
        retrying={sources.isFetching}
        onRetry={() => void sources.refetch()}
      />
    );
  }
  if (hidden || items.length === 0) {
    return (
      <EmptyState
        icon={Receipt}
        headingLevel={3}
        title="No billing source covers this project."
        description={
          hidden
            ? "Ask an organization admin to connect a billing source."
            : "Connect the provider's billing to compare what it billed with what Spanlight computed."
        }
        action={
          hidden ? undefined : (
            <Button variant="primary" asChild>
              <Link to="/$orgId/$projectId/settings/billing-sources" params={params}>
                Connect a billing source
              </Link>
            </Button>
          )
        }
        className="rounded-tile bg-surface-muted"
      />
    );
  }
  return (
    <SourceSummaryTable
      items={items}
      days={window ? { first: window.first, last: window.last } : null}
    />
  );
}
