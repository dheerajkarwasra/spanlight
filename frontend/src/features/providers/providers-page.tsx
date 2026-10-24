import { CircleCheck } from "lucide-react";

import { PageHeader } from "@/components/page-header";
import { useProjectFilters } from "@/features/shell/project-context";
import type { HealthReport } from "@/lib/api";
import { useNow } from "@/lib/use-now";
import { bucketFor, type ResolvedRange } from "@/lib/time-range";

import { ErrorsByClassChart } from "./errors-by-class-chart";
import { isHealthyWindow } from "./healthy-window";
import { IncidentList } from "./incident-list";
import { ProviderCard } from "./provider-card";
import { ProvidersEmpty, ProvidersError, ProvidersSkeleton } from "./providers-page-states";
import { retrySummary } from "./providers-format";
import { useProviderHealth } from "./providers-queries";
import { RateLimitKindsTable } from "./rate-limit-kinds-table";
import { RetryCostTile } from "./retry-cost-tile";
import { SampleTraces } from "./sample-traces";

/**
 * Provider health: errors by provider and class, why providers refused
 * calls, what retries cost, and the providers' own status page incidents beside them.
 */
export function ProvidersPage() {
  const health = useProviderHealth();
  const { range } = useProjectFilters();
  const now = useNow(60_000);

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Provider health"
        description="Errors by provider and class, why providers refused calls, what retries cost, next to the providers’ own status pages."
      />
      {health.isPending ? (
        <ProvidersSkeleton />
      ) : health.isError ? (
        <ProvidersError
          error={health.error}
          onRetry={() => void health.refetch()}
          retrying={health.isFetching}
        />
      ) : hasCalls(health.data) ? (
        <ProvidersBody report={health.data} range={range} now={now} />
      ) : (
        <>
          <ProvidersEmpty range={range} />
          <IncidentList now={now} />
        </>
      )}
      {/* Announced once for assistive tech; the skeletons themselves are aria-hidden. */}
      <p className="sr-only" role="status" aria-live="polite">
        {health.isPending ? "Loading provider health" : ""}
      </p>
    </div>
  );
}

function hasCalls(report: HealthReport): boolean {
  return report.providers.some((provider) => provider.llm_calls > 0);
}

interface ProvidersBodyProps {
  report: HealthReport;
  range: ResolvedRange;
  now: Date;
}

function ProvidersBody({ report, range, now }: ProvidersBodyProps) {
  const providers = report.providers.filter((provider) => provider.llm_calls > 0);
  return (
    <>
      <div className="flex flex-wrap gap-4">
        {providers.map((provider) => (
          <div
            key={provider.provider ?? "unknown"}
            className="flex min-w-[min(100%,18rem)] flex-[2_1_18rem] flex-col *:flex-1"
          >
            <ProviderCard provider={provider} />
          </div>
        ))}
        <div className="flex min-w-[min(100%,13rem)] flex-[1_1_13rem] flex-col *:flex-1">
          <RetryCostTile summary={retrySummary(providers)} note={report.retry_cost_note} />
        </div>
      </div>
      {isHealthyWindow(report) ? (
        <p className="flex items-center gap-2 px-1 text-sm text-muted-foreground">
          <CircleCheck aria-hidden className="size-4 shrink-0 text-success" />
          No failed calls in this window.
        </p>
      ) : (
        <>
          <ErrorsByClassChart
            providers={providers.map((provider) => provider.provider)}
            bucket={bucketFor(range.durationMs)}
          />
          <RateLimitKindsTable providers={providers} />
          <SampleTraces windowMs={range.durationMs} />
        </>
      )}
      <IncidentList now={now} />
    </>
  );
}
