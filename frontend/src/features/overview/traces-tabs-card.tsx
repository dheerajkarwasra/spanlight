import { Link } from "@tanstack/react-router";
import { ArrowUpRight } from "lucide-react";

import { ErrorState } from "@/components/error-state";
import { StatusDot } from "@/components/status-dot";
import { Card, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useProjectParams } from "@/features/shell/project-context";
import { cn } from "@/lib/utils";

import { TraceTiles } from "./latest-traces";
import { RecentErrorsList } from "./recent-errors";
import { SLOWEST_SORT, useTracesTabQuery, type TracesTab } from "./use-overview-queries";

const TAB_LABELS: Record<TracesTab, string> = {
  errors: "Recent errors",
  slowest: "Slowest",
  latest: "Latest",
};

/** Each tab's "View all": the traces list with the same filter or sort, and its spoken name. */
const VIEW_ALL = {
  errors: { search: { status: "error" }, label: "failed traces" },
  slowest: { search: SLOWEST_SORT, label: "traces, slowest first" },
  latest: { search: {}, label: "traces" },
} as const;

const TABS: TracesTab[] = ["errors", "slowest", "latest"];

interface TracesTabsCardProps {
  now: Date;
  className?: string;
}

/** The "To do" traces card (spec §4 O3): recent errors, the slowest and the latest traces. */
export function TracesTabsCard({ now, className }: TracesTabsCardProps) {
  return (
    <Card className={cn("@container flex min-w-0 flex-col gap-3 p-5 sm:p-6", className)}>
      <Tabs defaultValue="errors" className="flex flex-col">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <CardTitle>Traces</CardTitle>
          <TabsList aria-label="Traces">
            {TABS.map((tab) => (
              <TabsTrigger key={tab} value={tab}>
                {TAB_LABELS[tab]}
              </TabsTrigger>
            ))}
          </TabsList>
        </div>
        {TABS.map((tab) => (
          <TabsContent key={tab} value={tab}>
            <TabPanel tab={tab} now={now} />
          </TabsContent>
        ))}
      </Tabs>
    </Card>
  );
}

/** Mounted only while its tab is open, so its query fetches (and Latest polls) only then. */
function TabPanel({ tab, now }: { tab: TracesTab; now: Date }) {
  const { orgId, projectId } = useProjectParams();
  const query = useTracesTabQuery(tab);
  if (query.isPending) {
    return (
      <div aria-hidden className="flex flex-col gap-1.5">
        {Array.from({ length: 3 }, (_, index) => (
          <Skeleton key={index} className="h-[52px] rounded-tile" />
        ))}
      </div>
    );
  }
  if (query.isError) {
    return (
      <ErrorState
        compact
        error={query.error}
        title="Couldn't load these traces"
        onRetry={() => {
          void query.refetch();
        }}
      />
    );
  }
  const traces = query.data.items;
  return (
    <div className="flex flex-col gap-3">
      <div className={cn("transition-opacity", query.isPlaceholderData && "opacity-60")}>
        {tab === "errors" ? (
          <RecentErrorsList traces={traces} now={now} />
        ) : (
          <TraceTiles traces={traces} now={now} />
        )}
      </div>
      {traces.length > 0 ? (
        <div className="flex items-center justify-between gap-3">
          {query.isLive ? (
            <span className="inline-flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
              <StatusDot state="ok" pulse />
              Live
              <span className="sr-only">: refreshes automatically</span>
            </span>
          ) : (
            <span />
          )}
          <Link
            to="/$orgId/$projectId/traces"
            params={{ orgId, projectId }}
            search={VIEW_ALL[tab].search}
            className="inline-flex min-h-6 items-center gap-1 rounded-sm text-sm font-semibold text-accent hover:underline hover:underline-offset-4"
          >
            View all
            <span className="sr-only"> {VIEW_ALL[tab].label}</span>
            <ArrowUpRight aria-hidden className="size-3.5" />
          </Link>
        </div>
      ) : null}
    </div>
  );
}
