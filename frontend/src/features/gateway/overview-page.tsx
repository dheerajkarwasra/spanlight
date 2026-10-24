import { Link } from "@tanstack/react-router";
import { KeyRound, Waypoints } from "lucide-react";

import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useProjectParams } from "@/features/shell";
import { cn } from "@/lib/utils";

import { FaultChips, FaultChipsSkeleton } from "./fault-chips";
import { GatewayLayout } from "./gateway-layout";
import { useGatewayKeysQuery } from "./gateway-queries";
import { KeyTable, KeyTableSkeleton } from "./key-table";
import { buildGatewayKpis } from "./overview-format";
import { OverviewKpis, OverviewKpisSkeleton } from "./overview-kpis";
import { useGatewayOverviewQuery } from "./overview-queries";
import { RequestsChart } from "./requests-chart";
import { TargetTable, TargetTableSkeleton } from "./target-table";

/** Gateway traffic of a window: KPIs, requests over time, per target and per key, and Lab faults. */
export function GatewayOverviewPage() {
  const keys = useGatewayKeysQuery();
  const noKeys = keys.data?.length === 0;
  // Only claim "Your keys are ready" once the keys have loaded and there is one.
  const hasKeys = (keys.data?.length ?? 0) > 0;
  const { overview, window } = useGatewayOverviewQuery(!noKeys);

  // No keys at all means no gateway traffic can exist, whatever the overview says.
  if (noKeys) {
    return (
      <GatewayLayout>
        <NoTraffic />
      </GatewayLayout>
    );
  }

  const data = overview.data;
  return (
    <GatewayLayout>
      <div className="flex flex-col gap-6">
        {overview.isError && !data ? (
          <Card>
            <ErrorState
              error={overview.error}
              title="Couldn’t load the gateway overview"
              onRetry={() => {
                void overview.refetch();
              }}
            />
          </Card>
        ) : hasKeys && data?.requests === 0 && !overview.isPlaceholderData ? (
          <NoRequests />
        ) : data ? (
          <div
            aria-busy={overview.isPlaceholderData}
            className={cn(
              "flex flex-col gap-6 transition-opacity",
              overview.isPlaceholderData && "opacity-60",
            )}
          >
            <OverviewKpis kpis={buildGatewayKpis(data, window.value)} />
            <RequestsChart series={data.series} window={window.value} />
            <TargetTable targets={data.by_target} />
            <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1fr)_21rem]">
              <KeyTable keys={data.by_key} />
              <FaultChips faults={data.faults} />
            </div>
          </div>
        ) : (
          <OverviewSkeleton />
        )}
        {/* Announce loading once for assistive tech; the skeletons themselves are aria-hidden. */}
        <p className="sr-only" role="status" aria-live="polite">
          {overview.isPending ? "Loading gateway overview" : ""}
        </p>
      </div>
    </GatewayLayout>
  );
}

function OverviewSkeleton() {
  return (
    <div className="flex flex-col gap-6">
      <OverviewKpisSkeleton />
      <Skeleton className="h-[236px] rounded-card" />
      <TargetTableSkeleton />
      <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1fr)_21rem]">
        <KeyTableSkeleton />
        <FaultChipsSkeleton />
      </div>
    </div>
  );
}

/** The project has no gateway keys: point to the page that creates one. */
function NoTraffic() {
  const { orgId, projectId } = useProjectParams();
  return (
    <Card>
      <EmptyState
        icon={Waypoints}
        title="No gateway traffic yet"
        description="Point an OpenAI or Anthropic SDK at your gateway URL and calls show up here."
        className="py-16"
        action={
          <Button asChild variant="primary">
            <Link to="/$orgId/$projectId/gateway/keys" params={{ orgId, projectId }}>
              Create a gateway key
            </Link>
          </Button>
        }
      />
    </Card>
  );
}

/** Keys exist but the window holds no requests: one empty state instead of zero tiles. */
function NoRequests() {
  const { orgId, projectId } = useProjectParams();
  return (
    <Card>
      <EmptyState
        icon={KeyRound}
        title="No requests in this window"
        description="Your keys are ready. Point a client at the base URL and requests show up here."
        className="py-16"
        action={
          <Button asChild variant="primary">
            <Link to="/$orgId/$projectId/gateway/keys" params={{ orgId, projectId }}>
              View keys
            </Link>
          </Button>
        }
      />
    </Card>
  );
}
